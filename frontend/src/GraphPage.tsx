import {useMemo,useRef,useState} from 'react';
import {ArrowRight, Maximize2, Network, Search, Snowflake, ZoomIn, ZoomOut} from 'lucide-react';
import {day,percent} from './api';
import type {MemoryEvent} from './types';
import {layoutMemories} from './memoryGraph';
import './memoryGraph.css';

export function GraphPage({events,onOpen}:{events:MemoryEvent[];onOpen:(id:string)=>void}){
  const [mode,setMode]=useState('network'),[selected,setSelected]=useState(''),[hover,setHover]=useState(''),[query,setQuery]=useState('');
  const [view,setView]=useState({x:0,y:0,zoom:1});
  const drag=useRef<{x:number;y:number;vx:number;vy:number;moved:boolean}|null>(null);
  const svg=useRef<SVGSVGElement>(null);
  const visible=useMemo(()=>[...events].sort((a,b)=>b.learned_at.localeCompare(a.learned_at)).filter(e=>!query || `${e.title} ${e.body}`.toLowerCase().includes(query.toLowerCase())).slice(0,250),[events,query]);
  const {nodes,edges,lanes}=useMemo(()=>layoutMemories(visible,mode==='time'),[visible,mode]);
  const focus=hover || selected;
  const neighbours=new Set([focus]);
  edges.forEach(e=>{if(e.source.event.id===focus)neighbours.add(e.target.event.id);if(e.target.event.id===focus)neighbours.add(e.source.event.id);});
  const current=visible.find(e=>e.id===selected);
  const related=edges.filter(e=>e.source.event.id===selected || e.target.event.id===selected);
  const prominent=new Set([...nodes].sort((a,b)=>b.degree-a.degree).slice(0,8).map(n=>n.event.id));
  const color=(e:MemoryEvent)=>e.frozen?'#7099bd':e.memory_status==='active'?'#78916c':e.memory_status==='quiet'?'#c2a274':e.memory_status==='future'?'#a28fb1':'#bac1b2';
  const reset=()=>setView({x:0,y:0,zoom:1});
  const zoom=(amount:number)=>setView(v=>({...v,zoom:Math.max(.45,Math.min(3,v.zoom+amount))}));
  const coords=(x:number,y:number)=>{const point=new DOMPoint(x,y);return svg.current?.getScreenCTM() ? point.matrixTransform(svg.current.getScreenCTM()!.inverse()):point;};
  return <div className="memory-network-page">
    <section className="page-heading"><div><div className="eyebrow">THE MEMORY ATLAS</div><h1>让经历，连成线<span>。</span></h1><p>在关联里找到线索，也沿着时间回看经过。</p></div><div className="atlas-total"><strong>{events.length}</strong><span>件记忆 · 持续生长</span></div></section>
    <div className="atlas-shell">
      <section className="atlas-canvas">
        <header className="atlas-toolbar"><div className="atlas-tabs" role="group" aria-label="网络布局">{[['network','关联网络'],['time','时间脉络']].map(([value,label])=><button key={value} aria-pressed={mode===value} className={mode===value?'active':''} onClick={()=>{setMode(value);reset();}}>{label}</button>)}</div><label className="atlas-search"><Search size={14}/><input aria-label="搜索图中的记忆" placeholder="寻找一段记忆…" value={query} onChange={e=>{setQuery(e.target.value);setSelected('');reset();}}/></label></header>
        <div className="atlas-stage">
          <div className="atlas-caption"><span>{mode==='time'?'CHRONOLOGY':'CONNECTED MOMENTS'}</span><small>{nodes.length} 个节点 / {edges.length} 条关联{events.length>250?' · 最多展示 250 件，可搜索定位':''}</small></div>
          <svg ref={svg} viewBox="0 0 900 560" role="group" aria-label="可拖动、缩放的记忆关联图" onPointerDown={e=>{if(e.button!==0 || (e.target as Element).closest('[data-node]'))return;const p=coords(e.clientX,e.clientY);drag.current={x:p.x,y:p.y,vx:view.x,vy:view.y,moved:false};e.currentTarget.setPointerCapture(e.pointerId);}} onPointerMove={e=>{const d=drag.current;if(!d)return;const p=coords(e.clientX,e.clientY);if(Math.hypot(p.x-d.x,p.y-d.y)>3)d.moved=true;setView(v=>({...v,x:d.vx+p.x-d.x,y:d.vy+p.y-d.y}));}} onPointerUp={e=>{if(drag.current && !drag.current.moved)setSelected('');drag.current=null;if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);}} onPointerCancel={()=>{drag.current=null;}}>
            <g transform={`translate(${450*(1-view.zoom)+view.x},${280*(1-view.zoom)+view.y}) scale(${view.zoom})`}>
              {mode==='time' && lanes.map((l,i)=><g key={l.date} className="atlas-lane"><line x1={l.x} y1="80" x2={l.x} y2="480"/>{(lanes.length<10 || i%Math.ceil(lanes.length/9)===0 || i===lanes.length-1) && <text x={l.x} y="510" textAnchor="middle">{l.date}</text>}</g>)}
              {edges.map(e=>{const active=!!focus && (e.source.event.id===focus || e.target.event.id===focus);const dx=e.target.x-e.source.x,dy=e.target.y-e.source.y;return <path key={e.id} d={`M${e.source.x},${e.source.y} Q${(e.source.x+e.target.x)/2-dy*.13},${(e.source.y+e.target.y)/2+dx*.13} ${e.target.x},${e.target.y}`} fill="none" stroke={active?'#a58b62':'#acb8a3'} strokeWidth={active?1.8:.8+e.strength*.6} opacity={focus?(active?.85:.09):.35}/>;})}
              {nodes.map(n=>{const id=n.event.id,active=id===focus,near=neighbours.has(id);const r=5+Math.min(8,Math.sqrt(n.degree)*2)+Math.max(0,n.event.memory.strength)*2;const label=active || (focus?near:(nodes.length<=22 || prominent.has(id)));return <g key={id} data-node="true" role="button" tabIndex={0} aria-label={n.event.title} className="atlas-node" opacity={focus&&!near?.22:1} onMouseEnter={()=>setHover(id)} onMouseLeave={()=>setHover('')} onFocus={()=>setHover(id)} onBlur={()=>setHover('')} onClick={()=>setSelected(id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setSelected(id);}}}>
                <title>{n.event.title}</title><circle cx={n.x} cy={n.y} r={r+11} fill={color(n.event)} opacity={active?.17:.06}/><circle cx={n.x} cy={n.y} r={r+4} fill="none" stroke={color(n.event)} strokeWidth={active?1.4:.6} opacity={active?1:.3}/><circle cx={n.x} cy={n.y} r={r} fill={color(n.event)} stroke="#fffefa" strokeWidth="2"/>
                {label && <text x={n.x} y={n.y+r+20} textAnchor="middle" className={active?'focused':''}>{n.event.title.slice(0,active?26:12)}{n.event.title.length>(active?26:12)?'…':''}</text>}
              </g>;})}
            </g>
          </svg>
          {!nodes.length && <div className="atlas-empty"><Network size={32}/><strong>{events.length?'没有匹配的记忆':'这里的联系尚待发生'}</strong><span>{events.length?'换一个搜索词试试':'整理一些事件后，记忆会在这里相遇。'}</span></div>}
          <div className="atlas-bottom"><div className="atlas-legend"><span><i style={{background:'#78916c'}}/>鲜明</span><span><i style={{background:'#c2a274'}}/>渐淡</span><span><i style={{background:'#bac1b2'}}/>沉睡</span><span><i style={{background:'#a28fb1'}}/>计划</span><span><i style={{background:'#7099bd'}}/>冻结</span></div><div className="atlas-tools"><button onClick={()=>zoom(-.2)} aria-label="缩小图谱"><ZoomOut size={15}/></button><span>{Math.round(view.zoom*100)}%</span><button onClick={()=>zoom(.2)} aria-label="放大图谱"><ZoomIn size={15}/></button><button onClick={reset} aria-label="重置视图"><Maximize2 size={14}/></button></div></div>
        </div>
        <footer className="atlas-footer">拖动画布移动 · 点击节点聚焦 · 连线仅表示已有事件关联{mode==='time'?' · 日期按天等距排列，无发生时间时使用得知时间':''}</footer>
      </section>
      <aside className="atlas-sidebar"><div className="eyebrow">A CLOSER LOOK</div>{current?<><div className="atlas-event-meta">{day(current.occurred_at || current.learned_at)}{current.frozen && <span><Snowflake size={12}/>已冻结</span>}</div><h2>{current.title}</h2><p className="atlas-excerpt">{current.body.slice(0,300)}{current.body.length>300?'…':''}</p><div className="atlas-strength"><span>当前鲜明度</span><b>{percent(current.memory.strength)}</b></div><button className="button secondary" onClick={()=>onOpen(current.id)}>阅读完整记忆<ArrowRight size={14}/></button><div className="atlas-related"><h3>沿线想起 <span>{related.length}</span></h3>{related.map(e=>{const target=e.source.event.id===selected?e.target.event:e.source.event;return <button key={e.id} onClick={()=>setSelected(target.id)}><small>{e.relation}</small><span>{target.title}<ArrowRight size={12}/></span></button>;})}{!related.length&&<p>这件事暂时没有直接关联。</p>}</div></>:<div className="atlas-welcome"><div className="atlas-orbit"><Network size={28}/></div><h2>每段经历都有回响</h2><p>选择一颗记忆节点，看看它和哪些事情相连。</p><div className="atlas-help"><span>01</span><p>节点越大，连接通常越多。</p><span>02</span><p>悬停时突出相邻记忆，点击后可沿关联继续探索。</p><span>03</span><p>切到时间脉络，按日期回看事情的发展。</p></div></div>}</aside>
    </div>
  </div>;
}
