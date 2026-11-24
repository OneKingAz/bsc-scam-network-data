import csv,re,collections,sys
rows=list(csv.DictReader(open(r'D:\bot\analysis\hop_trades.csv',encoding='utf-8-sig'),delimiter=';'))
print(len(rows))
c=collections.Counter()
for r in rows:
    k=re.sub(r'[0-9.]+','N',r['reason'])[:40]
    c[k]+=1
for k,v in c.most_common(40):print(v,k)
print(rows[-1]); print(rows[900]); print(rows[1500])
