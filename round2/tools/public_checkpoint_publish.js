// Run this file's source in functions.exec after preparing a checkpoint.
// Requires the connected GitHub tools; no token or shell Git credential needed.
const repo = 'lancheq/chimera-agent-cloud';
function checked(r) { if (r.isError) throw new Error('GitHub request rejected'); return r.structuredContent; }
function shellQuote(s) { return "'" + s.replaceAll("'", "'\\''") + "'"; }
async function command(cmd, max_output_tokens=2000) {
  const r=await tools.exec_command({cmd,max_output_tokens});
  if(r.exit_code!==0) throw new Error('Checkpoint operation failed: '+r.output.slice(0,500));
  return r.output;
}
const info=JSON.parse(await command("python - <<'PY'\nfrom pathlib import Path\nimport json,hashlib\np=Path('output/public_sync/pending.json');a=json.loads(p.read_text());bp=Path(a['bundle_path']);b=json.loads(bp.read_text());chunks=[];part={};size=0\nfor k,v in b['files'].items():\n if part and size+len(v.encode())>85000:chunks.append(part);part={};size=0\n part[k]=v;size+=len(v.encode())\nif part:chunks.append(part)\nfor i,c in enumerate(chunks):(bp.parent/f'publish-chunk-{i}.json').write_text(json.dumps(c,ensure_ascii=False))\nhashes={}\nfor k,v in b['files'].items():\n raw=v.encode();hashes[k]=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\\0'+raw).hexdigest()\nprint(json.dumps({'directory':str(bp.parent),'chunks':len(chunks),'checkpoint':b['checkpoint'],'local_archive_commit':b['local_archive_commit'],'hashes':hashes}))\nPY",10000));
const files={};
for(let start=0;start<info.chunks;start+=3) {
 const group=await Promise.allSettled(Array.from({length:Math.min(3,info.chunks-start)},(_,i)=>
  command('cat '+shellQuote(info.directory+'/publish-chunk-'+(start+i)+'.json'),50000)));
 for(const r of group) {if(r.status!=='fulfilled')throw r.reason; Object.assign(files,JSON.parse(r.value));}
}
const base=JSON.parse(checked(await tools.mcp__codex_apps__github_fetch({url:'https://api.github.com/repos/'+repo+'/branches/main'})).content);
const oldTree=JSON.parse(checked(await tools.mcp__codex_apps__github_fetch({url:'https://api.github.com/repos/'+repo+'/git/trees/'+base.commit.sha+'?recursive=1'})).content);
if(oldTree.truncated)throw new Error('Remote tree truncated; cannot safely compare managed paths');
const remoteFiles=Object.fromEntries(oldTree.tree.filter(x=>x.type==='blob').map(x=>[x.path,x.sha]));
const elements=Object.entries(files).filter(([p])=>remoteFiles[p]!==info.hashes[p]).map(([path,content])=>({path,mode:'100644',type:'blob',content}));
// round2 is exclusively generated; preserve original release and all archives.
for(const path of Object.keys(remoteFiles))if(path.startsWith('round2/')&&!(path in files))elements.push({path,mode:'100644',type:'blob',sha:null});
const oldReadme=checked(await tools.mcp__codex_apps__github_fetch_file({repository_full_name:repo,path:'README.md',ref:base.commit.sha})).content;
if(!oldReadme.startsWith('# 第二轮研究进度与 Chat 阅读入口')) elements.push({path:'README.md',mode:'100644',type:'blob',content:'# 第二轮研究进度与 Chat 阅读入口\n\n[当前状态](discussion/CURRENT.md) · [最新存档](discussion/LATEST.md) · [实验索引](discussion/EXPERIMENT_INDEX.md) · [第二轮代码与报告](round2/README.md)\n\n根目录保留上一轮发布代码。\n\n---\n\n'+oldReadme});
const tree=checked(await tools.mcp__codex_apps__github_create_tree({repository_full_name:repo,base_tree_sha:base.commit.commit.tree.sha,tree_elements:elements}));
const commit=checked(await tools.mcp__codex_apps__github_create_commit({repository_full_name:repo,parent_sha:base.commit.sha,tree_sha:tree.sha,message:'Checkpoint '+info.checkpoint+'\n\nCode and public process reports archived together; private clinical data and model assets excluded.'}));
checked(await tools.mcp__codex_apps__github_update_ref({repository_full_name:repo,branch_name:'main',sha:commit.sha,expected_sha:base.commit.sha,force:false}));
const after=JSON.parse(checked(await tools.mcp__codex_apps__github_fetch({url:'https://api.github.com/repos/'+repo+'/branches/main'})).content);
if(after.commit.sha!==commit.sha)throw new Error('Remote head changed; checkpoint needs reconciliation');
const verified=JSON.parse(checked(await tools.mcp__codex_apps__github_fetch({url:'https://api.github.com/repos/'+repo+'/git/trees/'+commit.sha+'?recursive=1'})).content);
if(verified.truncated)throw new Error('Verification tree truncated');
const actual=Object.fromEntries(verified.tree.filter(x=>x.type==='blob').map(x=>[x.path,x.sha]));
for(const [path,sha] of Object.entries(info.hashes))if(actual[path]!==sha)throw new Error('Remote file mismatch: '+path);
const latest=checked(await tools.mcp__codex_apps__github_fetch_file({repository_full_name:repo,path:'discussion/LATEST.md',ref:commit.sha}));
if(latest.content!==files['discussion/LATEST.md'])throw new Error('Latest checkpoint readback mismatch');
const receipt={status:'published_and_readback_verified',repository:repo,branch:'main',checkpoint:info.checkpoint,local_archive_commit:info.local_archive_commit,remote_commit:commit.sha,verified_file_count:Object.keys(info.hashes).length,commit_url:'https://github.com/'+repo+'/commit/'+commit.sha};
const save="import json,sys;from pathlib import Path;from datetime import datetime;from zoneinfo import ZoneInfo;r=json.loads(sys.argv[1]);p=Path('output/public_sync/pending.json');a=json.loads(p.read_text());assert a['checkpoint']==r['checkpoint'];d=Path(a['bundle_path']).parent;r['verified_beijing']=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat();t=json.dumps(r,ensure_ascii=False,indent=2)+'\\n';(d/'published_receipt.json').write_text(t);Path('output/public_sync/latest_published.json').write_text(t);p.unlink();print(r['commit_url'])";
await command('python -c '+shellQuote(save)+' '+shellQuote(JSON.stringify(receipt)));
text(receipt);
