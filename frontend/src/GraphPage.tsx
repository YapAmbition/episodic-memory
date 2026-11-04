import {useEffect,useMemo,useRef,useState} from 'react';
import type {Graph, GraphData, NodeData} from '@antv/g6';
import {ArrowRight, CalendarDays, Maximize2, Network, Search, Snowflake, ZoomIn, ZoomOut, X, Sparkles} from 'lucide-react';
import {day,percent} from './api';
import type {MemoryEvent} from './types';
import {prepareMemories, memoryColor, eventDate} from './memoryGraph';
import './memoryGraph.css';

type Mode = 'network' | 'time';
export function GraphPage({events,onOpen}:{events:MemoryEvent[];onOpen:(id:string)=>void}){
  const [mode,setMode]=useState<Mode>('network'),[selected,setSelected]=useState(''),[query,setQuery]=useState('');
  const [ready,setReady]=useState(false),[busy,setBusy]=useState(true),[error,setError]=useState(''),[zoom,setZoom]=useState(1);
  const [reduceMotion,setReduceMotion]=useState(()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const container=useRef<HTMLDivElement>(null), graph=useRef<Graph|null>(null);
  const queue=useRef<Promise<unknown>>(Promise.resolve()), generation=useRef(0), layoutKey=useRef('');
  const model=useMemo(()=>prepareMemories(events,query),[events,query]);
  // API polling must not continuously restart the physics/animation.
  const signature=JSON.stringify([model.nodes.map(n=>[n.event.id,n.event.title,eventDate(n.event),n.degree,n.matched,n.event.frozen,n.event.memory_status,Math.round(n.event.memory.strength*100)]),model.edges.map(e=>[e.id,e.source,e.target,e.relation,e.strength])]);
  const modelRef=useRef(model);modelRef.current=model;
  const current=model.nodes.find(n=>n.event.id===selected)?.event;
  const related=model.edges.filter(e=>e.source===selected || e.target===selected);
  const duration=reduceMotion ? false : {duration:550};

  useEffect(()=>{
    const media=window.matchMedia('(prefers-reduced-motion: reduce)');
    const change=()=>setReduceMotion(media.matches);media.addEventListener('change',change);
    return ()=>media.removeEventListener('change',change);
  },[]);
  useEffect(()=>{
    let disposed=false;let observer:ResizeObserver|undefined;
    void import('@antv/g6').then(({Graph})=>{
      if(disposed || !container.current)return;
      const g=new Graph({container:container.current,width:container.current.clientWidth,height:container.current.clientHeight,
        padding:70,animation:{duration:550},zoomRange:[.08,3],behaviors:['drag-canvas','zoom-canvas'],
        node:{state:{selected:{label:true,halo:true,haloStroke:'#b59a6c',haloLineWidth:12,lineWidth:3},dim:{opacity:.16},near:{halo:true,haloLineWidth:7}}},
        edge:{state:{selected:{stroke:'#b29563',lineWidth:2.2,opacity:.9},dim:{opacity:.06}}}});
      graph.current=g;
      g.on('node:click',event=>{if('target' in event && event.target && 'id' in event.target && typeof event.target.id==='string')setSelected(event.target.id);});
      g.on('canvas:click',()=>setSelected(''));
      g.on('aftertransform',()=>{if(!disposed && g.rendered && !g.destroyed)setZoom(g.getZoom());});
      observer=new ResizeObserver(()=>{if(!disposed && container.current)g.setSize(container.current.clientWidth,container.current.clientHeight);});
      observer.observe(container.current);setReady(true);
    }).catch(e=>{if(!disposed){setError(String(e));setBusy(false);}});
    return ()=>{disposed=true;generation.current++;observer?.disconnect();graph.current?.destroy();graph.current=null;};
  },[]);

  useEffect(()=>{
    if(!ready)return;
    const ticket=++generation.current;
    setBusy(true);setError('');
    queue.current=queue.current.catch(()=>{}).then(async()=>{
      const g=graph.current;if(!g || ticket!==generation.current || g.destroyed)return;
      const {nodes,edges}=modelRef.current;
      const topology=JSON.stringify([mode,nodes.map(n=>[n.event.id,eventDate(n.event)]),edges.map(e=>[e.source,e.target])]);
      const changed=topology!==layoutKey.current;
      const previous=new Map(g.getNodeData().map(n=>[n.id,n.style]));
      const prominent=new Set([...nodes].sort((a,b)=>b.degree-a.degree).slice(0,12).map(n=>n.event.id));
      const data:GraphData={nodes:nodes.map(n=>{
        const e=n.event,c=memoryColor(e),old=changed?undefined:previous.get(e.id);
        return {id:e.id,type:mode==='time'?'rect':'circle',data:{title:e.title},style:{
          x:mode==='time'?n.timeX:(old?.x ?? n.x),y:mode==='time'?n.timeY:(old?.y ?? n.y),
          size:mode==='time'?[224,82]:20+Math.min(20,Math.sqrt(n.degree)*5),radius:12,
          fill:mode==='time'?'#fffefa':c,stroke:mode==='time'?c:'#fffefa',lineWidth:mode==='time'?1.5:2,
          shadowColor:mode==='time'?'#677c6512':c,shadowBlur:mode==='time'?12:7,
          halo:mode==='network',haloStroke:c,haloLineWidth:7,haloStrokeOpacity:.1,
          labelText:mode==='time'?`${eventDate(e)}\n${e.title}`:e.title,
          label:mode==='time'||nodes.length<28||prominent.has(e.id)|| (!!query && n.matched),
          labelPlacement:mode==='time'?'center':'bottom',labelOffsetY:mode==='time'?0:9,
          labelFontSize:mode==='time'?12:11,labelLineHeight:19,labelFill:'#53695d',
          labelWordWrap:true,labelMaxWidth:mode==='time'?198:118,labelMaxLines:mode==='time'?3:2,
          labelBackground:true,labelBackgroundFill:'#fffefa',labelBackgroundFillOpacity:.9,labelBackgroundRadius:4,labelPadding:3,
          cursor:'pointer'}} as NodeData;
      }),edges:edges.map(e=>({id:e.id,source:e.source,target:e.target,type:mode==='time'?'cubic-horizontal':'quadratic',
        style:{stroke:'#8fa69a',opacity:mode==='time'?.28:.32,lineWidth:.6+e.strength,curveOffset:16,endArrow:mode==='time',endArrowSize:5}}))};
      g.setOptions({animation:reduceMotion?false:{duration:550}});
      g.setBehaviors(['drag-canvas','zoom-canvas',...(mode==='network'?['drag-element',{type:'fix-element-size',state:'',node:{shape:'label'},edgeFilter:()=>false},{type:'auto-adapt-label',padding:6}]:[])]);
      g.setData(data);
      if(changed){
        g.setLayout(mode==='network'?{type:'d3-force',animation:!reduceMotion,iterations:180,alphaDecay:.045,manyBody:{strength:-230},link:{distance:135,strength:.22},collide:{radius:42,strength:.9},center:{x:0,y:0}}:[]);
        await g.render();
        if(ticket!==generation.current || g.destroyed)return;
        layoutKey.current=topology;
        if(nodes.length){
          if(mode==='time') {await g.zoomTo(.9,false);await g.focusElement(nodes[0].event.id,false);await g.translateBy([-g.getSize()[0]/2+160,-g.getSize()[1]/2+140],reduceMotion?false:{duration:550});}
          else await g.fitView({when:'always'},reduceMotion?false:{duration:550});
        }
      }else await g.draw();
      if(ticket===generation.current){setBusy(false);setZoom(g.getZoom());}
    }).catch(e=>{if(ticket===generation.current){setError(e instanceof Error?e.message:String(e));setBusy(false);}});
  },[ready,signature,mode,reduceMotion]);

  useEffect(()=>{
    if(!ready)return;
    const ticket=generation.current;
    queue.current=queue.current.catch(()=>{}).then(async()=>{
      const g=graph.current;if(!g || g.destroyed || ticket!==generation.current)return;
      const m=modelRef.current,focus=m.nodes.some(n=>n.event.id===selected)?selected:'';
      const near=new Set([focus]);m.edges.forEach(e=>{if(e.source===focus)near.add(e.target);if(e.target===focus)near.add(e.source);});
      const states:Record<string,string[]>={};
      m.nodes.forEach(n=>{states[n.event.id]=!focus?[]:n.event.id===focus?['selected']:near.has(n.event.id)?['near']:['dim'];});
      m.edges.forEach(e=>{states[e.id]=!focus?[]:e.source===focus||e.target===focus?['selected']:['dim'];});
      await g.setElementState(states, !reduceMotion);
      if(focus)await g.focusElement(focus,reduceMotion?false:{duration:450});
    }).catch(()=>{});
  },[ready,selected,signature,mode,reduceMotion]);

  const action=(fn:(g:Graph)=>Promise<unknown>)=>{const g=graph.current;if(g&&!busy)void fn(g).catch(e=>setError(String(e)));};
  return <div className="memory-network-page">
    <section className="page-heading"><div><div className="eyebrow">THE MEMORY ATLAS</div><h1>让经历，连成线<span>。</span></h1><p>从一件小事出发，沿着联系，走回那一天。</p></div><div className="atlas-total"><strong>{events.length}</strong><span>件共同经历</span></div></section>
    <div className="atlas-shell">
      <section className="atlas-canvas">
        <header className="atlas-toolbar"><div className="atlas-tabs" role="group" aria-label="网络布局">{([['network','关联网络'],['time','时间脉络']] as const).map(([value,label])=><button key={value} aria-pressed={mode===value} className={mode===value?'active':''} onClick={()=>{setSelected('');setMode(value);}}>{value==='network'?<Network size={14}/>:<CalendarDays size={14}/>} {label}</button>)}</div><label className="atlas-search"><Search size={15}/><input aria-label="搜索图中的记忆" placeholder="一个名字，一段往事…" value={query} onChange={e=>{setQuery(e.target.value);setSelected('');}}/>{query&&<button aria-label="清空搜索" onClick={()=>setQuery('')}><X size={13}/></button>}</label></header>
        <div className={`atlas-stage atlas-stage-${mode}`}>
          <div ref={container} className="atlas-g6" role="group" aria-label={mode==='time'?'可缩放的事件时间脉络':'可拖动的事件关联网络'} />
          <div className="atlas-caption"><span>{mode==='time'?'ONE DAY, THEN ANOTHER':'EVERYTHING IS CONNECTED'}</span><small>{model.nodes.length} 件记忆 · {model.edges.length} 条关联{query?` · 命中 ${model.matches} 件，保留直接关联`:''}</small></div>
          {busy&&<div className="atlas-loading"><Sparkles size={13}/>正在展开记忆</div>}
          {error&&<div role="alert" className="atlas-error">图谱加载失败：{error}</div>}
          {!busy&&!model.nodes.length&&<div className="atlas-empty"><Network size={32}/><strong>{events.length?'没有找到这段记忆':'这里的联系尚待发生'}</strong><span>{events.length?'试试一个名字或细节':'整理事件后，在这里看见它们的联系'}</span></div>}
          <div className="atlas-bottom"><div className="atlas-legend">{[['#6c927a','鲜明'],['#bd9c66','渐淡'],['#a9b4ae','沉睡'],['#a28ab9','计划'],['#6f96bc','冻结']].map(([c,t])=><span key={t}><i style={{background:c}}/>{t}</span>)}</div><div className="atlas-tools"><button disabled={busy} onClick={()=>action(g=>g.zoomTo(Math.max(.08,g.getZoom()/1.25),duration))} aria-label="缩小图谱"><ZoomOut size={15}/></button><span>{Math.round(zoom*100)}%</span><button disabled={busy} onClick={()=>action(g=>g.zoomTo(Math.min(3,g.getZoom()*1.25),duration))} aria-label="放大图谱"><ZoomIn size={15}/></button><button disabled={busy} onClick={()=>action(g=>g.fitView({when:'always'},duration))} aria-label="查看全图"><Maximize2 size={15}/></button></div></div>
        </div>
        <footer className="atlas-footer">{mode==='network'?'拖动节点调整位置 · 滚轮缩放 · 点击记忆聚焦':'按日期分列 · 拖动画布沿时间探索 · 点击卡片查看关联'}{model.omitted>0?` · 还有 ${model.omitted} 件未展示，请搜索定位`:''}</footer>
      </section>
      <aside className="atlas-sidebar"><div className="eyebrow">{current?'A CLOSER LOOK':'FOLLOW A MEMORY'}</div>{current?<><div className="atlas-event-meta">{day(current.occurred_at || current.learned_at)}{current.frozen&&<span><Snowflake size={12}/>已冻结</span>}</div><h2>{current.title}</h2><p className="atlas-excerpt">{current.body.slice(0,300)}{current.body.length>300?'…':''}</p><div className="atlas-strength"><span>当前鲜明度</span><b>{percent(current.memory.strength)}</b></div><button className="button secondary" onClick={()=>onOpen(current.id)}>阅读完整记忆<ArrowRight size={14}/></button><div className="atlas-related"><h3>沿线想起 <span>{related.length}</span></h3>{related.map(e=>{const target=model.nodes.find(n=>n.event.id===(e.source===selected?e.target:e.source))!.event;return <button key={e.id} onClick={()=>setSelected(target.id)}><small>{e.relation}</small><span>{target.title}<ArrowRight size={12}/></span></button>;})}{!related.length&&<p>这件事暂时没有直接关联。</p>}</div></>:<><div className="atlas-welcome"><div className="atlas-orbit"><Network size={28}/></div><h2>一些小事，<br/>串起我们的日常。</h2><p>选择一件记忆，看见它的来处，<br/>也看看后来发生了什么。</p></div><div className="atlas-browse"><small>{query?'搜索结果与相邻记忆':'从这里开始'}</small>{model.nodes.slice(0,30).map(n=><button key={n.event.id} onClick={()=>setSelected(n.event.id)}><i style={{background:memoryColor(n.event)}}/><span>{n.event.title}</span><ArrowRight size={12}/></button>)}</div></>}</aside>
    </div>
  </div>;
}
