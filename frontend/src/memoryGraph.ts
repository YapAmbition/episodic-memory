import type { MemoryEvent } from './types';

export function layoutMemories(events: MemoryEvent[], timeline = false) {
  const ordered = [...events].sort((a,b) => (a.occurred_at || a.learned_at).localeCompare(b.occurred_at || b.learned_at) || a.id.localeCompare(b.id));
  const nodes = ordered.map((event,i) => ({event, x:450 + Math.cos(i*2.39996)*Math.sqrt(i+1)*24, y:285 + Math.sin(i*2.39996)*Math.sqrt(i+1)*24, degree:0}));
  const lookup = new Map(nodes.map(n=>[n.event.id,n]));
  const edges = nodes.flatMap(source=>source.event.links.flatMap((link,i)=>{
    const target=lookup.get(link.target_id);
    if(!target || source===target)return [];
    source.degree++;target.degree++;
    return [{source,target,relation:link.relation,strength:link.strength,id:`${source.event.id}-${i}`}];
  }));
  const days = [...new Set(ordered.map(e=>(e.occurred_at || e.learned_at).slice(0,10)))];
  const lanes = days.map((date,i)=>({date,x:days.length===1 ? 450 : 90+i*720/(days.length-1)}));
  if(timeline){
    const groups = new Map<string,typeof nodes>();
    nodes.forEach(n=>{const day=(n.event.occurred_at || n.event.learned_at).slice(0,10);groups.set(day,[...(groups.get(day)||[]),n]);});
    lanes.forEach(l=>{const group=groups.get(l.date)!;group.forEach((n,i)=>{n.x=l.x; n.y=group.length===1?285:110+i*340/(group.length-1);});});
  }else{
    // Fixed iterations and deterministic initialization keep refreshes stable.
    for(let step=0;step<180;step++){
      const force=nodes.map(n=>({x:(450-n.x)*.009,y:(285-n.y)*.009}));
      for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
        const dx=nodes[i].x-nodes[j].x,dy=nodes[i].y-nodes[j].y;
        const distance=Math.max(1,Math.hypot(dx,dy));
        const strength=1700/(distance*distance)+(distance<42?(42-distance)*.14:0);
        force[i].x+=dx/distance*strength;force[i].y+=dy/distance*strength;
        force[j].x-=dx/distance*strength;force[j].y-=dy/distance*strength;
      }
      const indices=new Map(nodes.map((n,i)=>[n.event.id,i]));
      edges.forEach(e=>{const a=indices.get(e.source.event.id)!,b=indices.get(e.target.event.id)!;
        const dx=e.target.x-e.source.x,dy=e.target.y-e.source.y,d=Math.max(1,Math.hypot(dx,dy));
        const f=(d-95)*.016;force[a].x+=dx/d*f;force[a].y+=dy/d*f;force[b].x-=dx/d*f;force[b].y-=dy/d*f;
      });
      nodes.forEach((n,i)=>{n.x+=Math.max(-7,Math.min(7,force[i].x));n.y+=Math.max(-7,Math.min(7,force[i].y));});
    }
    if(nodes.length){
      const minX=Math.min(...nodes.map(n=>n.x)),maxX=Math.max(...nodes.map(n=>n.x));
      const minY=Math.min(...nodes.map(n=>n.y)),maxY=Math.max(...nodes.map(n=>n.y));
      const scale=Math.min(1.7,680/Math.max(1,maxX-minX),370/Math.max(1,maxY-minY));
      nodes.forEach(n=>{n.x=450+(n.x-(minX+maxX)/2)*scale;n.y=275+(n.y-(minY+maxY)/2)*scale;});
    }
  }
  return {nodes,edges,lanes};
}
