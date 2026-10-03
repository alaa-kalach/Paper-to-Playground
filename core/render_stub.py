"""Minimal but complete renderer used only when template/render.py is missing or crashes.
Self-contained HTML: inline CSS/JS, MathML via latex2mathml, SVG bars/heatmaps/lines."""
import html as H
import json
import re


def tex(s, display=False):
    try:
        from latex2mathml.converter import convert
        return convert(s, display="block" if display else "inline")
    except Exception:
        return f"<code>{H.escape(str(s))}</code>"


def safe_json(obj):
    return (json.dumps(obj, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def badge(src):
    return f'<span class="b {"bp" if src == "paper" else "bo"}">{"paper" if src == "paper" else "our example"}</span>'


CSS = """body{font:16px/1.5 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:16px;color:#1d2433;background:#fafbfc}
h1{font-size:1.6em}section{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:12px 16px;margin:12px 0}
.b{font-size:.7em;padding:1px 6px;border-radius:8px;margin-left:6px}.bp{background:#dbeafe}.bo{background:#fde68a}
.ctl{margin:8px 0}.ctl input[type=number]{width:4.5em}.out{display:inline-block;vertical-align:top;margin:8px 16px 8px 0}
table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:2px 6px;text-align:right}.warn{color:#b45309}
.insight{font-weight:600;margin:8px 0}button{margin:4px 4px 4px 0}"""

JS = r"""
const S=JSON.parse(document.getElementById('spec').textContent);
eval(S.compute); // compute() is defined by the spec and was checked in a sandbox
const st={}; for(const c of S.controls) st[c.id]=JSON.parse(JSON.stringify(c.default));
const f=v=>typeof v==='number'?(Math.abs(v)>=1e4||(Math.abs(v)<1e-3&&v!==0)?v.toExponential(2):(+v.toFixed(4)).toString()):String(v);
function ctlUI(){const box=document.getElementById('ctls');box.innerHTML='';
 for(const c of S.controls){const d=document.createElement('div');d.className='ctl';d.innerHTML='<b>'+c.label+'</b> ';
  const upd=()=>{draw();};
  if(c.type==='slider'){const i=document.createElement('input');i.type='range';i.min=c.min;i.max=c.max;i.step=c.step;i.value=st[c.id];
   const o=document.createElement('span');o.textContent=' '+f(st[c.id]);i.oninput=()=>{st[c.id]=+i.value;o.textContent=' '+f(+i.value);upd();};d.append(i,o);}
  else if(c.type==='toggle'){const i=document.createElement('input');i.type='checkbox';i.checked=st[c.id];i.onchange=()=>{st[c.id]=i.checked;upd();};d.append(i);}
  else if(c.type==='select'){const s=document.createElement('select');for(const o of c.options){const e=document.createElement('option');e.value=o.value;e.textContent=o.label;s.append(e);}s.value=st[c.id];s.onchange=()=>{st[c.id]=s.value;upd();};d.append(s);}
  else{const rows=c.type==='vector'?[st[c.id]]:st[c.id];const t=document.createElement('table');
   rows.forEach((r,ri)=>{const tr=document.createElement('tr');r.forEach((v,ci)=>{const td=document.createElement('td');const i=document.createElement('input');i.type='number';i.min=c.min;i.max=c.max;i.step=c.step;i.value=v;
    i.onchange=()=>{let x=Math.min(c.max,Math.max(c.min,+i.value||0));i.value=x;if(c.type==='vector')st[c.id][ci]=x;else st[c.id][ri][ci]=x;upd();};td.append(i);tr.append(td);});t.append(tr);});d.append(t);}
  box.append(d);}}
function svgBars(v,labels){const w=320,h=140,n=v.length,m=Math.max(1e-9,...v.map(Math.abs));let s='<svg width="'+w+'" height="'+(h+20)+'">';
 v.forEach((x,i)=>{const bw=w/n-6,bh=Math.abs(x)/m*(h/2-10),y=x>=0?h/2-bh:h/2;s+='<rect x="'+(i*w/n+3)+'" y="'+y+'" width="'+bw+'" height="'+bh+'" fill="#3b82f6"/><text x="'+(i*w/n+3+bw/2)+'" y="'+(h+14)+'" font-size="11" text-anchor="middle">'+((labels&&labels[i])||i+1)+': '+f(x)+'</text>';});
 return s+'<line x1="0" x2="'+w+'" y1="'+h/2+'" y2="'+h/2+'" stroke="#999"/></svg>';}
function tbl(m,rl,cl,heat){const mx=Math.max(1e-9,...m.flat().map(Math.abs));let s='<table>';if(cl)s+='<tr><th></th>'+cl.map(c=>'<th>'+c+'</th>').join('')+'</tr>';
 m.forEach((r,i)=>{s+='<tr>'+(rl?'<th>'+rl[i]+'</th>':'')+r.map(x=>'<td style="background:'+(heat&&typeof x==='number'?'rgba(59,130,246,'+(Math.abs(x)/mx*0.6)+')':'')+'">'+f(x)+'</td>').join('')+'</tr>';});return s+'</table>';}
function svgSeries(v,o){const w=420,h=220,p=36,xs=v.x,ys=v.lines.flatMap(l=>l.y).filter(Number.isFinite);const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(0,...ys),y1=Math.max(...ys);
 const X=x=>p+(x-x0)/((x1-x0)||1)*(w-2*p),Y=y=>h-p-(y-y0)/((y1-y0)||1)*(h-2*p),col=['#2563eb','#dc2626','#16a34a','#9333ea'];
 let s='<svg width="'+w+'" height="'+h+'"><line x1="'+p+'" y1="'+(h-p)+'" x2="'+(w-p)+'" y2="'+(h-p)+'" stroke="#999"/><line x1="'+p+'" y1="'+p+'" x2="'+p+'" y2="'+(h-p)+'" stroke="#999"/>';
 s+='<text x="'+w/2+'" y="'+(h-6)+'" font-size="11" text-anchor="middle">'+(o.x_label||'x')+' ['+f(x0)+', '+f(x1)+']</text><text x="4" y="'+(p-8)+'" font-size="11">'+(o.y_label||'y')+' max '+f(y1)+'</text>';
 v.lines.forEach((l,k)=>{s+='<polyline fill="none" stroke="'+col[k%4]+'" stroke-width="2" points="'+l.y.map((y,i)=>Number.isFinite(y)?X(xs[i])+','+Y(y):'').join(' ')+'"/><text x="'+(w-p)+'" y="'+(p+14*k)+'" font-size="11" fill="'+col[k%4]+'" text-anchor="end">'+l.name+'</text>';});
 if(v.marker)s+='<circle cx="'+X(v.marker.x)+'" cy="'+Y(v.marker.y)+'" r="5" fill="#f59e0b"/>';return s+'</svg>';}
function draw(){let r;try{r=compute(JSON.parse(JSON.stringify(st)));}catch(e){r={warning:'Computation error: '+e.message};}
 document.getElementById('insight').textContent=r.insight||'';document.getElementById('warn').textContent=r.warning||'';
 const box=document.getElementById('outs');box.innerHTML='';
 for(const o of S.outputs){const v=r[o.id];const d=document.createElement('div');d.className='out';let h='<b>'+o.label+(o.unit?' ('+o.unit+')':'')+'</b><br>';
  if(v===null||v===undefined)h+='—';else if(o.type==='scalar')h+='<span style="font-size:1.6em">'+f(v)+'</span>';else if(o.type==='vector')h+=svgBars(v,o.labels);
  else if(o.type==='matrix')h+=tbl(v,o.row_labels,o.col_labels,true);else if(o.type==='series')h+=svgSeries(v,o);
  else if(o.type==='table')h+=tbl(v.rows,null,v.columns,false);else if(o.type==='flow')h+='<ul>'+v.edges.map(e=>'<li>'+e.from+' → '+e.to+(e.label?' ('+e.label+')':'')+'</li>').join('')+'</ul>';
  d.innerHTML=h;box.append(d);}
 document.querySelectorAll('[data-ph]').forEach(el=>{const k=el.dataset.ph;const v=k in r?r[k]:st[k];el.textContent=v===undefined||v===null?'—':f(v);});}
function tryIt(i){const e=S.explorations[i];for(const c of S.controls)st[c.id]=JSON.parse(JSON.stringify(c.default));Object.assign(st,JSON.parse(JSON.stringify(e.set||{})));ctlUI();draw();document.getElementById('play').scrollIntoView({behavior:'smooth'});}
function resetAll(){for(const c of S.controls)st[c.id]=JSON.parse(JSON.stringify(c.default));ctlUI();draw();}
ctlUI();draw();
"""


def render(spec):
    s = spec
    p = s.get("paper") or {}
    e = lambda x: H.escape(str(x or ""))
    eq = s.get("equation") or {}
    sym_rows = "".join(f"<tr><td>{tex(x.get('symbol',''))}</td><td style='text-align:left'>{e(x.get('meaning'))}{badge(x.get('source'))}</td></tr>"
                       for x in s.get("symbols") or [] if isinstance(x, dict))
    steps = []
    for st in s.get("steps") or []:
        parts = re.split(r"\{\{\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\}\}", st)
        h = ""
        for i, part in enumerate(parts):
            h += tex(part) if i % 2 == 0 and part.strip() else (f'<b data-ph="{e(part)}"></b>' if i % 2 else "")
        steps.append(f"<li>{h}</li>")
    exps = ""
    for i, x in enumerate((s.get("explorations") or [])[:2]):
        ver = "" if x.get("verified", True) else " <i>(not automatically verified)</i>"
        exps += (f"<div><h3>{i+1}. {e(x.get('title'))}{ver}</h3><p><b>Predict:</b> {e(x.get('predict'))}</p>"
                 f"<button onclick='tryIt({i})'>Try it</button><button onclick='resetAll()'>Reset</button>"
                 f"<details><summary>Observe and why</summary><p><b>Observe:</b> {e(x.get('observe'))}</p><p><b>Why:</b> {e(x.get('why'))}</p></details></div>")
    claims = "".join(f"<li>{e(c.get('text'))}{badge(c.get('source'))}</li>" for c in s.get("claims") or [] if isinstance(c, dict))
    v = s.get("_verification") or {}
    cite = ", ".join(x for x in [e(p.get("title")), e(p.get("authors")), e(p.get("year")), e(p.get("section")), e(p.get("equation"))] if x)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(s.get('title'))}</title><style>{CSS}</style></head><body>
<h1>{e(s.get('title'))}</h1>
<section><h2>The idea</h2><p>{e(s.get('hook'))}</p></section>
<section><h2>Symbols</h2><table>{sym_rows}</table></section>
<section><h2>Equation {badge(eq.get('source'))}</h2>{tex(eq.get('latex',''), True)}<p>{e(eq.get('caption'))}</p></section>
<section id="play"><h2>Playground</h2><div id="ctls"></div><div class="insight" id="insight"></div><div class="warn" id="warn"></div><div id="outs"></div></section>
<section><h2>Step by step (live numbers)</h2><ol>{''.join(steps)}</ol></section>
<section><h2>Guided explorations</h2>{exps}</section>
<section><h2>Limitation</h2><p>{e(s.get('limitation'))}</p><h2>Common misconception</h2><p>{e(s.get('misconception'))}</p></section>
<section><h2>Source</h2><p>{cite}. <a href="{e(p.get('url'))}">{e(p.get('url'))}</a></p><ul>{claims}</ul>
<p><i>This toy demonstration illustrates the mechanism with small hand-picked inputs; it does not reproduce the paper's experiments or results.</i></p>
<p style="font-size:.85em">Automated checks: {e(v.get('summary',''))}</p></section>
<script type="application/json" id="spec">{safe_json(s)}</script>
<script>{JS}</script></body></html>"""
