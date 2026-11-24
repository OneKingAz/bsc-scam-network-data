import csv,re,collections,io,statistics as st
out=io.StringIO()
def P(*a):print(*a,file=out)
def f(x):
    try:return float(x.replace(',','.'))
    except:return 0.0
rows=list(csv.DictReader(open(r'D:\bot\analysis\hop_trades.csv',encoding='utf-8-sig'),delimiter=';'))
for r in rows:
    m=re.match(r'сеть (\d+)',r['reason']); r['net']=int(m.group(1)) if m else 1
# nets 3/4 signal trades >=14.11: sells and held
P('## Сторож сетей 3/4: сделки с удержанием ~2 с, 13-19.11, ставка не тест (>=0.001 BNB)')
for net in (3,4):
    s=[r for r in rows if 'сигнал' in r['reason'] and r['net']==net and r['time']>='2026-11-13' and f(r['stake_bnb'])>=0.001]
    zero=[r for r in s if f(r['got_bnb'])==0]
    held=[f(r['held_s']) for r in s]
    P(f'сеть {net}: n={len(s)} продаж с got=0: {len(zero)}; held медиана {st.median(held):.1f}, p90 {sorted(held)[int(len(held)*.9)]:.1f}, макс {max(held):.1f}; худший pnl% {min(f(r["pnl_pct"]) for r in s):.1f}; заметки с текстом: {sum(1 for r in s if r["note"])}')
    c=collections.Counter(r['note'][:50] for r in s if r['note']);P('  заметки',c.most_common(6))
# все сети: доля сделок -1.1 (проигрыш) по дням для сетей 1/2 после 18.11
P('## сети 1/2 с 18.11: pnl% распределение')
for net in (1,2):
    for d in ('2026-11-18','2026-11-19'):
        s=[f(r['pnl_pct']) for r in rows if 'сигнал' in r['reason'] and r['net']==net and r['time'].startswith(d) and f(r['stake_bnb'])>=0.001]
        if s:P(net,d,'n',len(s),'avg',round(sum(s)/len(s),2),'плюс',sum(x>0 for x in s),'медиана',round(st.median(s),2))
# daily USD for ставка>=0.001 signals only
P('## Реальные ставки (>=0.001) итог по дням, BNB')
d=collections.defaultdict(float)
for r in rows:
    if 'сигнал' in r['reason'] and f(r['stake_bnb'])>=0.001:d[r['time'][:10]]+=f(r['pnl_bnb'])
for k in sorted(d):P(k,round(d[k],5))
P('сумма',round(sum(d.values()),5))
# chain total
ch=[r for r in rows if 'сигнал' not in r['reason']]
P('цепочки/перекид: n',len(ch),'pnl',round(sum(f(r['pnl_bnb']) for r in ch),5))
# sol
srows=list(csv.DictReader(open(r'D:\bot\analysis\sol_trades.csv',encoding='utf-8-sig'),delimiter=';'))
P('## SOL всего строк',len(srows))
c=collections.Counter(r['mode'] for r in srows);P(c)
live=[r for r in srows if r['mode']=='LIVE']
P('LIVE n',len(live),'pnl_sol',round(sum(f(r['pnl_sol']) for r in live),5),'плюс',sum(f(r['pnl_sol'])>0 for r in live))
dd=collections.defaultdict(lambda:[0,0.0,0])
for r in live:
    k=r['time'][:5];dd[k][0]+=1;dd[k][1]+=f(r['pnl_sol']);dd[k][2]+=f(r['pnl_sol'])>0
for k in sorted(dd):P(k,dd[k])
c=collections.Counter(r['exit_reason'][:25] for r in live);P(c.most_common(10))
# creators with bad
cc=collections.defaultdict(lambda:[0,0.0])
for r in live:cc[r['creator']][0]+=1;cc[r['creator']][1]+=f(r['pnl_sol'])
P('по создателям (убыточные):',sorted([(k,v[0],round(v[1],4)) for k,v in cc.items()],key=lambda x:x[2])[:6])
# lp
lp=list(csv.DictReader(open(r'D:\bot\analysis\lp_auto.csv',encoding='utf-8-sig'),delimiter=';'))
P('## пул авто',len(lp),round(sum(f(r['pnl_bnb']) for r in lp),5))
# pretick
pt=list(csv.DictReader(open(r'D:\bot\analysis\pretick_test.csv',encoding='utf-8-sig'),delimiter=';'))
P('## pretick',len(pt))
open(r'D:\bot\analysis\review_2311\out_a2.txt','w',encoding='utf-8').write(out.getvalue())
