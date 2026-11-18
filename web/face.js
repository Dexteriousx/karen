/* Лицо Карен (из «Лицо Карен.html») для сайта.
   const f = KarenFace(canvas);  f.setFace('радость');  f.talking = true;  f.level = 0..1 (громкость голоса) */
window.KarenFace = function (cv) {
  const g = cv.getContext('2d'), W = 800, H = 480;
  const FACES = {
    'обычное':   {open:1,   brow:0,   browY:0,   curve:.35, mopen:0,   mw:70,  cheek:0, sz:1,   lx:0,  ly:0,   col:'#5ef2ff', wink:0},
    'радость':   {open:1,   brow:-.1, browY:-6,  curve:2.3, mopen:.8,  mw:200, cheek:.65, sz:1,   lx:0,  ly:0,   col:'#39ff6a', wink:0},
    'грусть':    {open:.75, brow:-1,  browY:6,   curve:-.9, mopen:0,   mw:60,  cheek:0, sz:1,   lx:0,  ly:.6,  col:'#7aa8ff', wink:0},
    'злость':    {open:.65, brow:1,   browY:10,  curve:-.5, mopen:0,   mw:85,  cheek:0, sz:1,   lx:0,  ly:0,   col:'#ff5a4d', wink:0},
    'удивление': {open:1.25,brow:-.2, browY:-22, curve:0,   mopen:1,   mw:38,  cheek:0, sz:1.1, lx:0,  ly:0,   col:'#ffffff', wink:0},
    'думает':    {open:.85, brow:.3,  browY:-4,  curve:-.05,mopen:0,   mw:42,  cheek:0, sz:1,   lx:-.9,ly:-.8, col:'#ffd166', wink:0},
    'сонная':    {open:.14, brow:0,   browY:0,   curve:.15, mopen:0,   mw:40,  cheek:0, sz:1,   lx:0,  ly:.4,  col:'#8fa6c8', wink:0},
    'слушает':   {open:1.1, brow:-.3, browY:-10, curve:.2,  mopen:0,   mw:50,  cheek:0, sz:1.05,lx:0,  ly:0,   col:'#9dff7a', wink:0},
    'подмигивает':{open:1,  brow:-.1, browY:-4,  curve:.9,  mopen:.15, mw:95,  cheek:.3, sz:1,   lx:0,  ly:0,   col:'#ff9de6', wink:1},
  };
  const NUM = ['open','brow','browY','curve','mopen','mw','cheek','sz','lx','ly','wink'];
  const hex = h => [1,3,5].map(i => parseInt(h.substr(i,2),16)), lerp = (a,b,k) => a+(b-a)*k;
  const cur = JSON.parse(JSON.stringify(FACES['обычное']));
  let target = FACES['обычное'], colCur = hex('#5ef2ff');
  let blink = 0, nextBlink = 2, lookTX = 0, lookTY = 0, nextLook = 1.5, mouthOpenNow = 0, talkMix = 0, last = performance.now();
  const api = { name: 'обычное', talking: false, level: 0, fakeTalk: false,
    setFace(n){ if (FACES[n]) { api.name = n; target = FACES[n]; } }, faces: Object.keys(FACES) };

  function rr(x,y,w,h,r){r=Math.min(r,w/2,h/2);g.beginPath();g.moveTo(x+r,y);g.arcTo(x+w,y,x+w,y+h,r);g.arcTo(x+w,y+h,x,y+h,r);g.arcTo(x,y+h,x,y,r);g.arcTo(x,y,x+w,y,r);g.closePath();}
  function eye(cx,cy,w,h,col,cheek){
    const hh=Math.max(6,h);
    g.save();g.shadowColor=col;g.shadowBlur=28;g.fillStyle=col;rr(cx-w/2,cy-hh/2,w,hh,Math.min(w,hh)*.45);g.fill();g.restore();
    if(cheek>0.01){g.fillStyle='#050608';g.beginPath();g.ellipse(cx,cy+hh*.5+8-cheek*hh*.55,w*.8,hh*.62,0,0,Math.PI*2);g.fill();}
  }
  function brow(cx,cy,w,tilt,col,side){
    const inner=-side, dy=tilt*22;
    g.save();g.strokeStyle=col;g.lineWidth=12;g.lineCap='round';g.shadowColor=col;g.shadowBlur=18;
    g.beginPath();g.moveTo(cx+inner*(w/2),cy+dy);g.lineTo(cx-inner*(w/2),cy-dy);g.stroke();g.restore();
  }
  function waves(col,amp,t,alpha){
    const cy=H/2,N=200, ws=[[1.6,2.2,0,1,8,1],[2.3,-2.9,1.7,.75,5,.65],[3.1,3.6,3.1,.55,4,.45],[1.1,-1.5,4.4,.85,3,.35]];
    g.save();g.strokeStyle=col;g.lineCap='round';g.lineJoin='round';g.shadowColor=col;g.shadowBlur=24;
    for(const [f,s,ph,a,lw,op] of ws){
      g.globalAlpha=alpha*op;g.lineWidth=lw;g.beginPath();
      for(let i=0;i<=N;i++){const u=i/N,x=u*W,env=Math.pow(Math.sin(u*Math.PI),2),y=cy+Math.sin(u*Math.PI*2*f+t*s+ph)*amp*a*env;i?g.lineTo(x,y):g.moveTo(x,y);}
      g.stroke();
    }
    g.restore();
  }
  function mouth(cx,cy,w,curve,open,col){
    g.save();g.strokeStyle=col;g.fillStyle=col;g.lineWidth=11;g.lineCap='round';g.lineJoin='round';g.shadowColor=col;g.shadowBlur=22;
    const c=curve*55;
    if(open>0.04){
      const oh=open*70;
      g.beginPath();g.moveTo(cx-w,cy);g.quadraticCurveTo(cx,cy+c,cx+w,cy);g.quadraticCurveTo(cx,cy+c+oh*1.2,cx-w,cy);g.closePath();
      g.fillStyle='#1a0b12';g.fill();g.stroke();
      g.save();g.clip();g.fillStyle='rgba(255,120,150,.55)';g.beginPath();g.ellipse(cx,cy+c*.5+oh*.95,w*.45,oh*.35,0,0,Math.PI*2);g.fill();g.restore();
    }else{g.beginPath();g.moveTo(cx-w,cy);g.quadraticCurveTo(cx,cy+c*2,cx+w,cy);g.stroke();}
    g.restore();
  }
  function frame(now){
    const dt=Math.min(.05,(now-last)/1000);last=now;
    const k=1-Math.pow(.0009,dt);
    for(const n of NUM) cur[n]=lerp(cur[n],target[n],k);
    const tc=hex(target.col); for(let i=0;i<3;i++) colCur[i]=lerp(colCur[i],tc[i],k);
    const col='rgb('+colCur.map(v=>v|0).join(',')+')';
    nextBlink-=dt;
    if(nextBlink<=0&&blink===0){blink=.0001;nextBlink=2+Math.random()*3.5;}
    let bl=1;
    if(blink>0){blink+=dt*7;bl=Math.abs(Math.cos(blink*Math.PI));if(blink>=1){blink=0;bl=1;}}
    nextLook-=dt;
    if(nextLook<=0){lookTX=(Math.random()-.5)*1.2;lookTY=(Math.random()-.5)*.8;nextLook=1+Math.random()*2.5;}
    const lx=cur.lx+lookTX*.35, ly=cur.ly+lookTY*.35, t=now/1000;
    let lvl=api.level;
    if(api.fakeTalk){const v=(Math.sin(t*14)+Math.sin(t*9.3+1)+Math.sin(t*5.1+2))/3; lvl=.15+Math.abs(v)*.5;}
    let mo=cur.mopen;
    if(api.talking) mo=Math.max(.12,lvl*1.6);
    mouthOpenNow=lerp(mouthOpenNow,mo,1-Math.pow(.00001,dt));
    g.fillStyle='#050608';g.fillRect(0,0,W,H);
    talkMix=lerp(talkMix,api.talking?1:0,1-Math.pow(.002,dt));
    if(talkMix>.01) waves(col,40+mouthOpenNow*150,t,talkMix);
    g.save();g.globalAlpha=Math.max(0,1-talkMix*1.1);
    const sz=cur.sz, ew=130*sz, eh=150*sz*cur.open*bl, ex=[255+lx*22,545+lx*22], ey=185+ly*18;
    eye(ex[0],ey,ew,eh,col,cur.cheek);
    eye(ex[1],ey,ew,eh*(1-cur.wink*.93),col,cur.cheek*(1-cur.wink));
    const by=ey-100*sz+cur.browY;
    if(Math.abs(cur.brow)>.05||cur.browY<-8){brow(ex[0],by,120,cur.brow,col,-1);brow(ex[1],by,120,cur.brow,col,1);}
    mouth(400+lx*12,335+ly*8,cur.mw,cur.curve,api.talking?cur.mopen:mouthOpenNow,col);
    g.restore();
    if(api.name==='слушает'){const p=(Math.sin(t*4)+1)/2;g.strokeStyle='rgba(157,255,122,'+(.15+p*.35)*(1-talkMix)+')';g.lineWidth=6;rr(14,14,W-28,H-28,40);g.stroke();}
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return api;
};
