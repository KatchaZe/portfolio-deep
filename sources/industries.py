"""Which Damodaran industry a company belongs to, for ANY ticker.

Every industry-level input (bottom-up beta, terminal ROIC ceiling, industry margin and
sales-to-capital, the company-type router) keys off one of Damodaran's ~94 US
industries. Until 2026-10 that came from a hand-typed per-ticker dict, so a stock added
later fell through to an 11-bucket sector map: every "Healthcare" name became a big
pharma, every "Industrials" name a machinery maker.

Resolution order, most specific first; the basis is returned so callers and the card can
say how sure the mapping is:
  1. "override"  INDUSTRY_BY_TICKER, for the few names whose listed industry misleads
                 (NVO is listed as Biotechnology, AXON as Aerospace & Defense);
  2. "industry"  the FMP profile industry (Morningstar-style), exact table below;
  3. "keyword"   an industry string the table has not seen, matched by keyword rules;
  4. "sector"    the FMP sector only, the coarse last resort.
Pure: no network, no I/O.
"""
import re

# The industry names exactly as Damodaran spells them on Betas.html (his typo
# "Heathcare" included). tests/test_industries.py checks every mapping lands on one.
FINANCIAL_INDUSTRIES = frozenset({
    "Bank (Money Center)", "Banks (Regional)", "Brokerage & Investment Banking",
    "Insurance (General)", "Insurance (Life)", "Insurance (Prop/Cas.)", "Reinsurance",
    # consumer lenders, card issuers, mortgage finance: their debt is funding, too
    "Financial Svcs. (Non-bank & Insurance)",
})

INDUSTRY_BY_TICKER = {
    "MSFT": "Software (System & Application)", "ORCL": "Software (System & Application)",
    "GOOGL": "Advertising", "GOOG": "Advertising", "META": "Advertising",
    "NVDA": "Semiconductor", "AVGO": "Semiconductor", "TSM": "Semiconductor",
    "AMD": "Semiconductor", "MU": "Semiconductor", "INTC": "Semiconductor",
    "ASML": "Semiconductor Equip", "AMAT": "Semiconductor Equip", "LRCX": "Semiconductor Equip",
    # FMP files NVO under Biotechnology; its business is a large-cap pharma
    "LLY": "Drugs (Pharmaceutical)", "PFE": "Drugs (Pharmaceutical)",
    "ABBV": "Drugs (Pharmaceutical)", "NVO": "Drugs (Pharmaceutical)",
    "MRK": "Drugs (Pharmaceutical)", "JNJ": "Drugs (Pharmaceutical)",
    "REGN": "Drugs (Biotechnology)", "VRTX": "Drugs (Biotechnology)",
    "AMGN": "Drugs (Biotechnology)", "MRNA": "Drugs (Biotechnology)",
    "UNH": "Healthcare Support Services", "ELV": "Healthcare Support Services",
    "CI": "Healthcare Support Services", "CVS": "Healthcare Support Services",
    "HIMS": "Heathcare Information and Technology",   # sic: his spelling
    "TMDX": "Healthcare Products", "ISRG": "Healthcare Products",
    # TASER devices are a minority of AXON's revenue; most revenue and nearly all the
    # growth are cloud software and body cameras sold on subscription
    "AXON": "Software (System & Application)",
    "RKLB": "Aerospace/Defense", "LMT": "Aerospace/Defense",
    "TSLA": "Auto & Truck", "GM": "Auto & Truck", "F": "Auto & Truck",
    "CELH": "Beverage (Soft)", "KO": "Beverage (Soft)", "PEP": "Beverage (Soft)",
    # MELI runs two businesses: see config.INDUSTRY_MIX
    "AMZN": "Retail (General)",
    # Payment networks file as "credit services" but carry no loan book: Damodaran's
    # Financial Svcs figures (unlevered beta 0.33, sales/capital 0.07) are a lender's
    # balance sheet. Judgment call: valued as the asset-light data networks they are.
    "V": "Information Services", "MA": "Information Services",
    "PYPL": "Information Services", "FI": "Information Services",
    "FIS": "Information Services", "GPN": "Information Services",
    "AAPL": "Computers/Peripherals",
}

# FMP / Morningstar industry -> Damodaran industry. Keys are normalized by _key().
FMP_INDUSTRY = {
    # basic materials
    "agricultural inputs": "Chemical (Basic)", "chemicals": "Chemical (Basic)",
    "chemicals - specialty": "Chemical (Specialty)", "specialty chemicals": "Chemical (Specialty)",
    "building materials": "Building Materials", "construction materials": "Building Materials",
    "lumber & wood production": "Paper/Forest Products", "paper & paper products": "Paper/Forest Products",
    "paper, lumber & forest products": "Paper/Forest Products",
    "aluminum": "Metals & Mining", "copper": "Metals & Mining",
    "other industrial metals & mining": "Metals & Mining", "industrial materials": "Metals & Mining",
    "uranium": "Metals & Mining",
    "gold": "Precious Metals", "silver": "Precious Metals",
    "other precious metals & mining": "Precious Metals", "other precious metals": "Precious Metals",
    "coking coal": "Coal & Related Energy", "thermal coal": "Coal & Related Energy", "coal": "Coal & Related Energy",
    "steel": "Steel",
    # communication services
    "advertising agencies": "Advertising", "broadcasting": "Broadcasting",
    "electronic gaming & multimedia": "Software (Entertainment)", "entertainment": "Entertainment",
    "internet content & information": "Software (Internet)", "publishing": "Publishing & Newspapers",
    "telecom services": "Telecom. Services", "telecommunications services": "Telecom. Services",
    # consumer cyclical
    "apparel manufacturing": "Apparel", "apparel - manufacturers": "Apparel", "textile manufacturing": "Apparel",
    "manufacturing - textiles": "Apparel", "luxury goods": "Apparel",
    "apparel retail": "Retail (Special Lines)", "apparel - retail": "Retail (Special Lines)",
    "auto & truck dealerships": "Retail (Automotive)", "auto - dealerships": "Retail (Automotive)",
    "auto manufacturers": "Auto & Truck", "auto - manufacturers": "Auto & Truck",
    "auto parts": "Auto Parts", "auto - parts": "Auto Parts",
    "recreational vehicles": "Recreation", "auto - recreational vehicles": "Recreation",
    "department stores": "Retail (General)", "discount stores": "Retail (General)",
    "footwear & accessories": "Shoe", "apparel - footwear & accessories": "Shoe",
    "furnishings, fixtures & appliances": "Furn/Home Furnishings",
    "gambling": "Hotel/Gaming", "resorts & casinos": "Hotel/Gaming",
    "gambling, resorts & casinos": "Hotel/Gaming", "lodging": "Hotel/Gaming", "travel lodging": "Hotel/Gaming",
    "home improvement retail": "Retail (Building Supply)", "home improvement": "Retail (Building Supply)",
    "internet retail": "Retail (General)", "specialty retail": "Retail (Special Lines)",
    "leisure": "Recreation", "packaging & containers": "Packaging & Container",
    "personal services": "Business & Consumer Services",
    "personal products & services": "Business & Consumer Services",
    "residential construction": "Homebuilding", "restaurants": "Restaurant/Dining",
    "travel services": "Business & Consumer Services",
    # consumer defensive
    "beverages - brewers": "Beverage (Alcoholic)", "beverages - alcoholic": "Beverage (Alcoholic)",
    "beverages - wineries & distilleries": "Beverage (Alcoholic)",
    "beverages - non-alcoholic": "Beverage (Soft)",
    "confectioners": "Food Processing", "food confectioners": "Food Processing",
    "packaged foods": "Food Processing",
    "education & training services": "Education",
    "farm products": "Farming/Agriculture", "agricultural farm products": "Farming/Agriculture",
    "agricultural - commodities/milling": "Farming/Agriculture",
    "food distribution": "Food Wholesalers", "grocery stores": "Retail (Grocery and Food)",
    "household & personal products": "Household Products", "tobacco": "Tobacco",
    # energy
    "oil & gas drilling": "Oilfield Svcs/Equip.", "oil & gas equipment & services": "Oilfield Svcs/Equip.",
    "oil & gas e&p": "Oil/Gas (Production and Exploration)",
    "oil & gas exploration & production": "Oil/Gas (Production and Exploration)",
    "oil & gas integrated": "Oil/Gas (Integrated)", "oil & gas energy": "Oil/Gas (Integrated)",
    "oil & gas refining & marketing": "Oil/Gas (Integrated)",
    "oil & gas midstream": "Oil/Gas Distribution", "solar": "Green & Renewable Energy",
    # financial services
    "asset management": "Investments & Asset Management",
    "asset management - global": "Investments & Asset Management",
    "asset management - income": "Investments & Asset Management",
    "asset management - bonds": "Investments & Asset Management",
    "asset management - leveraged": "Investments & Asset Management",
    "asset management - cryptocurrency": "Investments & Asset Management",
    "banks - diversified": "Bank (Money Center)", "banks": "Banks (Regional)",
    "banks - regional": "Banks (Regional)",
    "capital markets": "Brokerage & Investment Banking",
    "financial - capital markets": "Brokerage & Investment Banking",
    "investment - banking & investment services": "Brokerage & Investment Banking",
    "credit services": "Financial Svcs. (Non-bank & Insurance)",
    "financial - credit services": "Financial Svcs. (Non-bank & Insurance)",
    "financial conglomerates": "Financial Svcs. (Non-bank & Insurance)",
    "financial - conglomerates": "Financial Svcs. (Non-bank & Insurance)",
    "mortgage finance": "Financial Svcs. (Non-bank & Insurance)",
    "financial - mortgages": "Financial Svcs. (Non-bank & Insurance)",
    "financial data & stock exchanges": "Information Services",
    "financial - data & stock exchanges": "Information Services",
    "insurance - diversified": "Insurance (General)", "insurance - specialty": "Insurance (General)",
    "insurance - life": "Insurance (Life)", "insurance - property & casualty": "Insurance (Prop/Cas.)",
    "insurance - reinsurance": "Reinsurance",
    # brokers earn fees and carry no underwriting book: valued like a service firm
    "insurance brokers": "Financial Svcs. (Non-bank & Insurance)",
    "insurance - brokers": "Financial Svcs. (Non-bank & Insurance)",
    "shell companies": "Diversified",
    # healthcare
    "biotechnology": "Drugs (Biotechnology)",
    "drug manufacturers - general": "Drugs (Pharmaceutical)",
    "drug manufacturers - specialty & generic": "Drugs (Pharmaceutical)",
    "medical - pharmaceuticals": "Drugs (Pharmaceutical)",
    "healthcare plans": "Healthcare Support Services", "medical - healthcare plans": "Healthcare Support Services",
    "health information services": "Heathcare Information and Technology",
    "medical - healthcare information services": "Heathcare Information and Technology",
    "medical care facilities": "Hospitals/Healthcare Facilities",
    "medical - care facilities": "Hospitals/Healthcare Facilities",
    "medical devices": "Healthcare Products", "medical - devices": "Healthcare Products",
    "medical instruments & supplies": "Healthcare Products",
    "medical - instruments & supplies": "Healthcare Products",
    "medical - equipment & services": "Healthcare Products", "medical - specialties": "Healthcare Products",
    "diagnostics & research": "Healthcare Products", "medical - diagnostics & research": "Healthcare Products",
    "medical distribution": "Healthcare Support Services", "medical - distribution": "Healthcare Support Services",
    "pharmaceutical retailers": "Retail (Special Lines)",
    # industrials
    "aerospace & defense": "Aerospace/Defense",
    "airlines": "Air Transport", "airports & air services": "Air Transport",
    "airlines, airports & air services": "Air Transport",
    "building products & equipment": "Building Materials", "construction": "Engineering/Construction",
    "business equipment & supplies": "Office Equipment & Services",
    "conglomerates": "Diversified", "consulting services": "Business & Consumer Services",
    "electrical equipment & parts": "Electrical Equipment", "engineering & construction": "Engineering/Construction",
    "farm & heavy construction machinery": "Machinery", "agricultural - machinery": "Machinery",
    "industrial distribution": "Retail (Distributors)", "industrial - distribution": "Retail (Distributors)",
    "infrastructure operations": "Transportation", "industrial - infrastructure operations": "Transportation",
    "integrated freight & logistics": "Transportation", "marine shipping": "Shipbuilding & Marine",
    "metal fabrication": "Machinery", "manufacturing - metal fabrication": "Machinery",
    "pollution & treatment controls": "Environmental & Waste Services",
    "industrial - pollution & treatment controls": "Environmental & Waste Services",
    "railroads": "Transportation (Railroads)", "rental & leasing services": "Business & Consumer Services",
    "security & protection services": "Business & Consumer Services",
    "specialty business services": "Business & Consumer Services",
    "specialty industrial machinery": "Machinery", "industrial - machinery": "Machinery",
    "industrial - specialties": "Machinery", "manufacturing - miscellaneous": "Machinery",
    "staffing & employment services": "Business & Consumer Services",
    "tools & accessories": "Machinery", "manufacturing - tools & accessories": "Machinery",
    "trucking": "Trucking", "waste management": "Environmental & Waste Services",
    # real estate
    "real estate - development": "Real Estate (Development)",
    "real estate - diversified": "Real Estate (General/Diversified)",
    "real estate services": "Real Estate (Operations & Services)",
    "real estate - services": "Real Estate (Operations & Services)",
    "reit - retail": "Retail (REITs)",
    # technology
    "communication equipment": "Telecom. Equipment",
    "computer hardware": "Computers/Peripherals", "hardware, equipment & parts": "Electronics (General)",
    "consumer electronics": "Electronics (Consumer & Office)",
    "electronic components": "Electronics (General)",
    "electronics & computer distribution": "Retail (Distributors)", "technology distributors": "Retail (Distributors)",
    "information technology services": "Computer Services", "scientific & technical instruments": "Electronics (General)",
    "semiconductor equipment & materials": "Semiconductor Equip", "semiconductors": "Semiconductor",
    "software - application": "Software (System & Application)",
    "software - infrastructure": "Software (System & Application)",
    "software - services": "Software (System & Application)",
    # utilities
    "utilities - diversified": "Utility (General)", "diversified utilities": "Utility (General)",
    "general utilities": "Utility (General)",
    "utilities - regulated electric": "Utility (General)", "regulated electric": "Utility (General)",
    "utilities - regulated gas": "Utility (General)", "regulated gas": "Utility (General)",
    "utilities - regulated water": "Utility (Water)", "regulated water": "Utility (Water)",
    "utilities - renewable": "Power", "renewable utilities": "Power",
    "utilities - independent power producers": "Power", "independent power producers": "Power",
}

# An industry string FMP has not shown us before: first matching rule wins, so the
# more specific patterns come first.
KEYWORD_RULES = (
    (r"\breit\b|real estate investment trust", "R.E.I.T."),
    (r"reinsurance", "Reinsurance"),
    (r"insurance.*life|life insurance", "Insurance (Life)"),
    (r"property.*casualty|p&c", "Insurance (Prop/Cas.)"),
    (r"insurance", "Insurance (General)"),
    (r"\bbank", "Banks (Regional)"),
    (r"asset management|investment management", "Investments & Asset Management"),
    (r"semiconductor equip", "Semiconductor Equip"),
    (r"semiconductor", "Semiconductor"),
    (r"software", "Software (System & Application)"),
    (r"biotech", "Drugs (Biotechnology)"),
    (r"drug|pharma", "Drugs (Pharmaceutical)"),
    (r"medical|health", "Healthcare Products"),
    (r"oil|gas", "Oil/Gas (Production and Exploration)"),
    (r"gold|silver|precious", "Precious Metals"),
    (r"mining|metal", "Metals & Mining"),
    (r"chemical", "Chemical (Specialty)"),
    (r"utilit|electric|power", "Utility (General)"),
    (r"airline|air ", "Air Transport"),
    (r"restaurant", "Restaurant/Dining"),
    (r"retail|store", "Retail (Special Lines)"),
    (r"beverage", "Beverage (Soft)"),
    (r"food", "Food Processing"),
    (r"real estate", "Real Estate (Operations & Services)"),
    (r"auto", "Auto Parts"),
    (r"aerospace|defense", "Aerospace/Defense"),
    (r"telecom", "Telecom. Services"),
    (r"internet", "Software (Internet)"),
    (r"machinery|industrial|manufactur", "Machinery"),
)

# FMP's eleven sectors: the last resort.
SECTOR_TO_INDUSTRY = {
    "technology": "Software (System & Application)",
    "healthcare": "Drugs (Pharmaceutical)",
    "communication services": "Advertising",
    "consumer cyclical": "Retail (General)",
    "consumer defensive": "Food Processing",
    "industrials": "Machinery",
    "energy": "Oil/Gas (Production and Exploration)",
    "basic materials": "Chemical (Specialty)",
    "utilities": "Utility (General)",
    "real estate": "R.E.I.T.",
    "financial services": "Financial Svcs. (Non-bank & Insurance)",
}


def _key(text):
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ").replace("—", "-")).strip().lower()


def industry_mix(ticker):
    """{Damodaran industry: weight} for a company that runs several businesses
    (config.INDUSTRY_MIX), else None. Its largest business is its industry_for()."""
    import config
    mix = getattr(config, "INDUSTRY_MIX", {}).get((ticker or "").upper())
    return dict(mix) if mix else None


def industry_for(ticker, fmp_industry=None, sector=None):
    """(Damodaran industry, basis) or (None, None). basis is one of
    "override" / "industry" / "keyword" / "sector"; anything but "sector" is a
    company-level view, "sector" is the coarse bucket."""
    t = (ticker or "").upper().strip()
    mix = industry_mix(t)
    if mix:
        return max(mix, key=mix.get), "override"
    if t in INDUSTRY_BY_TICKER:
        return INDUSTRY_BY_TICKER[t], "override"
    k = _key(fmp_industry)
    if k:
        if k in FMP_INDUSTRY:
            return FMP_INDUSTRY[k], "industry"
        if k.startswith("reit"):
            return "R.E.I.T.", "industry"
        for pattern, ind in KEYWORD_RULES:
            if re.search(pattern, k):
                return ind, "keyword"
    ind = SECTOR_TO_INDUSTRY.get(_key(sector))
    return (ind, "sector") if ind else (None, None)


def is_financial(industry):
    """Balance-sheet financials: deposits/float are raw material, not capital, so
    FCFF, invested capital and WACC do not describe them (Damodaran)."""
    return industry in FINANCIAL_INDUSTRIES
