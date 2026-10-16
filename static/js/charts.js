// Chart.js draws on canvas where CSS cannot reach, so its fonts scale by --fs here
const __FS=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fs'))||1;
if(window.Chart){Chart.defaults.font.size=Math.round(12*__FS);}
const PALETTE=['#2563eb','#16a34a','#f59e0b','#dc2626','#7c3aed','#0d9488','#d97706','#0891b2','#be185d','#65a30d','#9333ea','#ea580c','#475569','#0ea5e9'];
let charts={};
// every chart goes through this one registry, so redrawing a canvas destroys its old chart
function mkChart(id,cfg){const ctx=document.getElementById(id);if(!ctx||typeof Chart==='undefined')return;if(charts[id])charts[id].destroy();charts[id]=new Chart(ctx,cfg);}
function makePie(id,data){
  const labels=data.map(d=>d.label),vals=data.map(d=>d.value),tot=vals.reduce((a,b)=>a+b,0)||1;
  // Legend at the bottom: on the right, Chart.js silently clips long entries in a 280px
  // box (their percentage disappears); at the bottom they get the full width and wrap.
  mkChart(id,{type:'doughnut',
    data:{labels:labels.map((l,i)=>`${l} ${(vals[i]/tot*100).toFixed(0)}%`),datasets:[{data:vals,backgroundColor:labels.map((_,i)=>PALETTE[i%PALETTE.length])}]},
    options:{maintainAspectRatio:false,
      plugins:{legend:{position:'bottom',labels:{font:{size:Math.round(10*__FS)},boxWidth:10,padding:6}}}}});
}
