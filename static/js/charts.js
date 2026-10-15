// ตัวคูณขนาดตัวหนังสือ (อ่านจาก --fs) — ใช้กับฟอนต์ใน Chart.js ที่ CSS เอื้อมไม่ถึง
const __FS=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fs'))||1;
if(window.Chart){Chart.defaults.font.size=Math.round(12*__FS);}
const PALETTE=['#2563eb','#16a34a','#f59e0b','#dc2626','#7c3aed','#0d9488','#d97706','#0891b2','#be185d','#65a30d','#9333ea','#ea580c','#475569','#0ea5e9'];
let charts={};
// ONE chart helper + ONE registry (2026-07-19): guard ctx -> destroy prior -> create
function mkChart(id,cfg){const ctx=document.getElementById(id);if(!ctx||typeof Chart==='undefined')return;if(charts[id])charts[id].destroy();charts[id]=new Chart(ctx,cfg);}
function makePie(id,data){
  const labels=data.map(d=>d.label),vals=data.map(d=>d.value),tot=vals.reduce((a,b)=>a+b,0)||1;
  // Legend at the BOTTOM, not the right. With position:'right' inside a 280px box the
  // doughnut took most of the width and Chart.js CLIPPED the longer entries — on the
  // live dashboard 'Defensive / Healthcare' and 'Communication / Media' lost their
  // percentage entirely and 'Growth / Technology 43%' lost its % sign, so two of five
  // sleeves could not be read at all. Chart.js does not wrap or ellipsize legend text,
  // it just cuts it, which is silent. At the bottom each entry gets the full width and
  // wraps onto extra rows instead.
  mkChart(id,{type:'doughnut',
    data:{labels:labels.map((l,i)=>`${l} ${(vals[i]/tot*100).toFixed(0)}%`),datasets:[{data:vals,backgroundColor:labels.map((_,i)=>PALETTE[i%PALETTE.length])}]},
    options:{maintainAspectRatio:false,
      plugins:{legend:{position:'bottom',labels:{font:{size:Math.round(10*__FS)},boxWidth:10,padding:6}}}}});
}
