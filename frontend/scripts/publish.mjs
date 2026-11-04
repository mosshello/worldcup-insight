import { readFileSync, writeFileSync, readdirSync, mkdirSync, copyFileSync, renameSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const source = join(root,'.ai/tmp/console-build');
const target = join(root,'web/console');
const html = readFileSync(join(source,'index.html'),'utf8').replace(/\r+\n/g,'\n');
function copyTree(from,to) {
  mkdirSync(to,{recursive:true});
  for (const entry of readdirSync(from,{withFileTypes:true})) {
    const path=join(from,entry.name), output=join(to,entry.name);
    if(entry.isDirectory()) copyTree(path,output);
    else copyFileSync(path,output);
  }
}
copyTree(join(source,'assets'),join(target,'assets'));
for (const match of html.matchAll(/(?:src|href)="\.\/([^"#]+)"/g)) {
  if (!existsSync(join(target,match[1]))) throw new Error('Missing published asset: '+match[1]);
}
function atomic(path,value) {
  writeFileSync(path+'.next',value);
  renameSync(path+'.next',path);
}
if(existsSync(join(target,'index.html'))) copyFileSync(join(target,'index.html'),join(target,'index.previous.html'));
atomic(join(target,'index.html'),html);
atomic(join(root,'web/club-preview.html'),html.replaceAll('./assets/','./console/assets/'));
console.log('Published verified assets; prior assets retained.');
