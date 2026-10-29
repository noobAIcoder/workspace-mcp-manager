const fs=require('node:fs'),{spawnSync}=require('node:child_process'),{Worker}=require('node:worker_threads');
console.log('direct',process.pid,process.memoryUsage());
if(!fs.readFileSync('/proc/self/stat','utf8').startsWith(process.pid+' '))throw Error('wrong process');
const c=spawnSync(process.execPath,['-e','console.log("child",process.pid,process.memoryUsage())'],{encoding:'utf8'});
console.log(c.stdout,c.stderr);if(c.status!==0)process.exit(c.status||1);
for(let i=0;i<3;i++)new Worker('console.log("worker",process.pid,process.memoryUsage())',{eval:true});
for(const [p,f] of [['/proc/1/stat','r'],['/proc/self/environ','r'],['/proc/self/stat','w'],['/proc/self/stat','r+']]){
 try{fs.openSync(p,f);throw Error('ALLOWED '+p)}catch(e){if(e.message.startsWith('ALLOWED'))throw e;console.log('denied',p,f,e.code)}
}
