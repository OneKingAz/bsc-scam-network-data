import json,zipfile,re
s=json.load(open('archive_stats.json',encoding='utf-8'))
print(s['files'],s['bytes']);
for p,w in s['excl']:print(w,'|',p)
z=zipfile.ZipFile(r'C:\Users\IT\Desktop\bsc_meme_bot\bsc_meme_bot_full_2026-11-23.zip','a',zipfile.ZIP_DEFLATED)
z.write(r'C:\Users\IT\Desktop\bsc_meme_bot\full_review_2026-11-23.md','full_review_2026-11-23.md')
z.write(r'C:\Users\IT\Desktop\bsc_meme_bot\CLAUDE.md','CLAUDE.md')
z.close()
z=zipfile.ZipFile(r'C:\Users\IT\Desktop\bsc_meme_bot\bsc_meme_bot_full_2026-11-23.zip')
n=z.namelist();print(len(n))
print([x for x in n if re.search(r'(?i)\.env$|config.*\.json$|nodekey|telegram|state_live',x)])
