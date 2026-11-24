"""20.11: four.meme BSC, BNB-quoted coins: paper grid «enter when the curve first holds >= X BNB, exit TP / SL / time»; 1% fee each side."""
import json,collections,math,statistics as st,sys
sys.stdout.reconfigure(encoding="utf-8")
BUY,SELL="0x0a5575b3","0x7db52723"
ev=collections.defaultdict(list)
for line in open('fourmeme_logs.jsonl',encoding='utf-8'):
    l=json.loads(line); t=l['topics'][0][:10]
    if t in (BUY,SELL):
        d=l['data'][2:]; w=[d[i:i+64] for i in range(0,len(d),64)]
        ev['0x'+w[0][-40:]].append((int(l['blockNumber'],16),t==BUY,int(w[2],16),int(w[7],16)/1e18))
LO,HI=(float(sys.argv[1]),float(sys.argv[2])) if len(sys.argv)>2 else (9.5,9.95)
bnb={c:e for c,e in ev.items() if LO<=math.log10(max(e[0][2],1))<=HI}
SC=float(sys.argv[3]) if len(sys.argv)>3 else 1.0
B0=min(e[0][0] for e in bnb.values()); B1=max(e[-1][0] for e in bnb.values()); mid=(B0+B1)//2
mx=sorted(max(x[3] for x in e) for e in bnb.values())
print("монет на BNB:",len(bnb),"макс BNB на кривой 99%%: %.2f, макс %.2f; дошли до 10: %d, до 15: %d"%(mx[int(.99*len(mx))],mx[-1],sum(1 for x in mx if x>=10),sum(1 for x in mx if x>=15)))
FEE=0.01
def sim(e,X,tp,sl,T):
    i=next((k for k,x in enumerate(e) if x[3]>=X),None)
    if i is None: return None
    b0=e[i][0]; p0=e[i][2]*1.01
    for x in e[i+1:]:
        r=x[2]/p0
        if x[0]-b0>T*133 or r>=1+tp or r<=1-sl: return r*(1-FEE)**2-1
    return e[-1][2]/p0*(1-FEE)**2-1
res=[]
for X in [x*SC for x in (0.3,0.5,1,2,4,8)]:
  for tp in (0.1,0.2,0.3,0.5,1.0):
    for sl in (0.1,0.2,0.3,0.5):
      for T in (2,5,15,60):
        a=[];h1=[];h2=[]
        for c,e in bnb.items():
            r=sim(e,X,tp,sl,T)
            if r is None: continue
            a.append(r); (h1 if e[0][0]<mid else h2).append(r)
        if len(a)>=20: res.append((st.mean(a),X,tp,sl,T,len(a),sum(1 for r in a if r>0)/len(a),st.mean(h1) if h1 else 0,st.mean(h2) if h2 else 0))
res.sort(reverse=True)
print("лучшие (среднее за сделку | вход BNB>=X | TP | SL | предел мин | сделок | в плюсе | 1-я/2-я половина суток):")
for r in res[:12]: print("  %+.1f%%  X=%s TP=%d%% SL=%d%% T=%d  n=%d  плюс %.0f%%  половины %+.1f%% / %+.1f%%"%(r[0]*100,r[1],r[2]*100,r[3]*100,r[4],r[5],r[6]*100,r[7]*100,r[8]*100))
print("всего вариантов",len(res),"в плюсе",sum(1 for r in res if r[0]>0), "медиана варианта %+.1f%%"%(st.median(r[0] for r in res)*100))
