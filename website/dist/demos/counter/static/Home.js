import{h as $h,list as $list,when as $when,signal as $signal,computed as $computed,mount as $mount,onMount as $onMount,py as $py,rpc as $rpc}from"./runtime.js";function Home($s){const count=$signal("count"in $s?$s["count"]:0);const step=$signal("step"in $s?$s["step"]:1);const doubled=$computed(()=>$py.mul(count(),2));function increment(){count($py.add(count(),step()));}
function reset(){count(0);}
return[$h("main",null,[$h("h1",null,["Counter"]),$h("button",{"id":"inc","onclick":increment},["Count: ",()=>count()]),$h("button",{"id":"reset","onclick":reset,"disabled":()=>(count()===0)},["Reset"]),$h("label",null,["Step ",$h("input",{"id":"step","type":"number","$bind":step})]),$h("p",{"id":"doubled"},["Doubled: ",()=>doubled()])])];}
$mount("Home",Home);
