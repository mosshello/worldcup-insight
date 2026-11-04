import {createServer} from 'node:http';
import {createReadStream,statSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=process.argv[2] ? path.resolve(process.argv[2]) : fileURLToPath(new URL('../../.ai/tmp/console-build/',import.meta.url));
const port=Number(process.argv[3] || 8897);
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'};
createServer((req,res)=>{try{const name=decodeURIComponent(new URL(req.url,'http://localhost').pathname);const file=path.resolve(root,'.'+(name==='/'?'/index.html':name));if(!file.startsWith(path.resolve(root)+path.sep)||!statSync(file).isFile()){res.writeHead(404);res.end();return;}res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream'});createReadStream(file).pipe(res);}catch{res.writeHead(404);res.end();}}).listen(port,'127.0.0.1',()=>process.stdout.write('Isolated preview ready\n'));
