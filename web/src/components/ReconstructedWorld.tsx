import { RoundedBox } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { getReconstructionModel, getReconstructionNavigation } from "../api";
import type { NavigationMetadata } from "../types";

type Props = { captureId?:string; desktopToken?:string; sample?:boolean; onExit:()=>void };

const CONTROL_KEYS = new Set(["ArrowUp","ArrowDown","ArrowLeft","ArrowRight","KeyW","KeyA","KeyS","KeyD","Space","KeyR"]);
const SAMPLE_BOOST_PADS:[number,number][]=[[-14,-9],[-7,9],[0,-10],[7,9],[14,-9],[-15,5],[15,5],[0,7]];
const SAMPLE_CHECKPOINTS:[number,number][]=[[0,8],[14,0],[0,-8],[-14,0]];
const SAMPLE_OBSTACLES={
  boxes:[[-11,-7,2,1.5],[11,7,2,1.5],[11,-7,2.25,1.5],[-11,7,2.25,1.5],[0,-10,3,.3],[0,10,3,.3]] as [number,number,number,number][],
  columns:[[-17,-11],[17,-11],[-17,11],[17,11]] as [number,number][],
};
const DRIVE_MAX_SPEED=120/11;
const BOOST_MAX_SPEED=150/11;

type RaceEvent={nextCheckpoint?:number;timeSeconds?:number;lapSeconds?:number;reset?:boolean};
type DriveBounds={minX:number;maxX:number;minZ:number;maxZ:number};
type WorldConfig={bounds:DriveBounds;spawn:[number,number];pads:[number,number][];checkpoints:[number,number][];obstacles:typeof SAMPLE_OBSTACLES|undefined};

function Kart({onSpeed,onBoost,onPointerLock,onRaceEvent,bounds,spawn,pads,checkpoints,obstacles}:{onSpeed:(speed:number)=>void;onBoost:(boost:number)=>void;onPointerLock:(locked:boolean)=>void;onRaceEvent:(event:RaceEvent)=>void;bounds:DriveBounds;spawn:[number,number];pads:[number,number][];checkpoints:[number,number][];obstacles:typeof SAMPLE_OBSTACLES|undefined}){
  const kart=useRef<THREE.Group>(null);
  const boostFlame=useRef<THREE.Group>(null);
  const speed=useRef(0);
  const heading=useRef(0);
  const cameraYaw=useRef(0);
  const cameraPitch=useRef(0);
  const boost=useRef(100);
  const boostingActive=useRef(false);
  const padCooldowns=useRef(new Map<number,number>());
  const softMouseCapture=useRef(false);
  const controlsActive=useRef(false);
  const raceClock=useRef(0);
  const lapStartedAt=useRef<number|null>(null);
  const nextCheckpoint=useRef(0);
  const checkpointInside=useRef(false);
  const lastReportedTenth=useRef(-1);
  const keys=useRef(new Set<string>());
  const {camera}=useThree();
  useEffect(()=>{
    const down=(event:KeyboardEvent)=>{if(!controlsActive.current)return;if(CONTROL_KEYS.has(event.code))event.preventDefault();keys.current.add(event.code);if(event.code==="KeyR"){speed.current=0;heading.current=0;cameraYaw.current=0;cameraPitch.current=0;kart.current?.position.set(spawn[0],.22,spawn[1])}};
    const up=(event:KeyboardEvent)=>keys.current.delete(event.code);
    window.addEventListener("keydown",down);window.addEventListener("keyup",up);
    return()=>{window.removeEventListener("keydown",down);window.removeEventListener("keyup",up)};
  },[spawn[0],spawn[1]]);
  useEffect(()=>{
    const releaseMouse=()=>{softMouseCapture.current=false;controlsActive.current=false;boostingActive.current=false;speed.current=0;keys.current.clear();document.body.style.cursor="";onSpeed(0);onPointerLock(false)};
    const pointerChanged=()=>{const locked=document.pointerLockElement instanceof HTMLCanvasElement;if(locked){softMouseCapture.current=true;controlsActive.current=true;document.body.style.cursor="none";onPointerLock(true)}else{releaseMouse()}};
    const mouseMoved=(event:MouseEvent)=>{if(document.pointerLockElement instanceof HTMLCanvasElement||softMouseCapture.current){cameraYaw.current-=event.movementX*.0028;cameraPitch.current=THREE.MathUtils.clamp(cameraPitch.current-event.movementY*.002,-.35,.45)}};
    const captureMouse=(canvas:HTMLCanvasElement)=>{softMouseCapture.current=true;controlsActive.current=true;document.body.style.cursor="none";onPointerLock(true);const request=canvas.requestPointerLock();if(request&&typeof request.catch==="function")void request.catch(()=>undefined)};
    const mouseDown=(event:MouseEvent)=>{if(event.button!==0||!(event.target instanceof HTMLCanvasElement))return;if(document.pointerLockElement!==event.target&&!softMouseCapture.current){captureMouse(event.target);return}if(boost.current>0)boostingActive.current=true};
    const mouseUp=(event:MouseEvent)=>{if(event.button===0)boostingActive.current=false};
    const escapeReleased=(event:KeyboardEvent)=>{if(event.code==="Escape"&&softMouseCapture.current){if(document.pointerLockElement instanceof HTMLCanvasElement)document.exitPointerLock();releaseMouse()}};
    const resumeGame=()=>{const canvas=document.querySelector(".world-stage canvas");if(canvas instanceof HTMLCanvasElement)captureMouse(canvas)};
    const restartGame=()=>{speed.current=0;heading.current=0;cameraYaw.current=0;cameraPitch.current=0;boostingActive.current=false;keys.current.clear();raceClock.current=0;lapStartedAt.current=null;nextCheckpoint.current=0;checkpointInside.current=false;lastReportedTenth.current=-1;kart.current?.position.set(spawn[0],.22,spawn[1]);onSpeed(0);onRaceEvent({reset:true,nextCheckpoint:0,timeSeconds:0})};
    document.addEventListener("pointerlockchange",pointerChanged);window.addEventListener("mousemove",mouseMoved);window.addEventListener("mousedown",mouseDown);window.addEventListener("mouseup",mouseUp);window.addEventListener("keydown",escapeReleased);window.addEventListener("gaussiancircuit:resume",resumeGame);window.addEventListener("gaussiancircuit:restart",restartGame);
    return()=>{document.removeEventListener("pointerlockchange",pointerChanged);window.removeEventListener("mousemove",mouseMoved);window.removeEventListener("mousedown",mouseDown);window.removeEventListener("mouseup",mouseUp);window.removeEventListener("keydown",escapeReleased);window.removeEventListener("gaussiancircuit:resume",resumeGame);window.removeEventListener("gaussiancircuit:restart",restartGame);if(document.pointerLockElement instanceof HTMLCanvasElement)document.exitPointerLock();releaseMouse()};
  },[onPointerLock,onRaceEvent,onSpeed,spawn[0],spawn[1]]);
  useFrame((_,rawDelta)=>{
    if(!kart.current)return;
    const delta=Math.min(rawDelta,.05);
    if(!controlsActive.current){boost.current=Math.min(100,boost.current+6*delta);if(boostFlame.current)boostFlame.current.visible=false;onSpeed(0);onBoost(Math.round(boost.current));return}
    {
      const forward=keys.current.has("KeyW")||keys.current.has("ArrowUp");
      const reverse=keys.current.has("KeyS")||keys.current.has("ArrowDown");
      const left=keys.current.has("KeyA")||keys.current.has("ArrowLeft");
      const right=keys.current.has("KeyD")||keys.current.has("ArrowRight");
      const braking=keys.current.has("Space");
      const boosting=boostingActive.current&&boost.current>0;
      if(forward)speed.current+=7*delta;
      if(reverse)speed.current-=5*delta;
      if(boosting){speed.current+=18*delta;boost.current=Math.max(0,boost.current-25*delta);if(boost.current<=0)boostingActive.current=false}else{boost.current=Math.min(100,boost.current+6*delta)}
      if(!forward&&!reverse)speed.current*=Math.pow(.2,delta);
      if(braking)speed.current*=Math.pow(.015,delta);
      speed.current=THREE.MathUtils.clamp(speed.current,-3.2,boosting?BOOST_MAX_SPEED:DRIVE_MAX_SPEED);
      const steering=(left?1:0)-(right?1:0);
      if(Math.abs(speed.current)>.08)heading.current+=steering*1.8*delta*Math.sign(speed.current)*(Math.min(Math.abs(speed.current),3)/3);
      const nextX=kart.current.position.x+Math.sin(heading.current)*speed.current*delta;
      const nextZ=kart.current.position.z+Math.cos(heading.current)*speed.current*delta;
      const hitsBox=obstacles?.boxes.some(([x,z,halfX,halfZ])=>Math.abs(nextX-x)<halfX+.62&&Math.abs(nextZ-z)<halfZ+.62)??false;
      const hitsColumn=obstacles?.columns.some(([x,z])=>Math.hypot(nextX-x,nextZ-z)<1.35)??false;
      if(nextX<bounds.minX||nextX>bounds.maxX||nextZ<bounds.minZ||nextZ>bounds.maxZ||hitsBox||hitsColumn){speed.current*=-.3}else{kart.current.position.x=nextX;kart.current.position.z=nextZ}
      kart.current.rotation.y=heading.current;
      const cameraHeading=heading.current+cameraYaw.current;
      const chaseOffset=new THREE.Vector3(-Math.sin(cameraHeading)*4.7,2.7+cameraPitch.current*2.4,-Math.cos(cameraHeading)*4.7).add(kart.current.position);
      camera.position.lerp(chaseOffset,1-Math.pow(.001,delta));
      camera.lookAt(kart.current.position.x,kart.current.position.y+.4,kart.current.position.z);
      if(boostFlame.current){boostFlame.current.visible=boosting;boostFlame.current.scale.z=.75+Math.random()*.6}
      pads.forEach(([x,z],index)=>{const availableAt=padCooldowns.current.get(index)??0;if(performance.now()>=availableAt&&Math.hypot(kart.current!.position.x-x,kart.current!.position.z-z)<1.05){boost.current=Math.min(100,boost.current+15);padCooldowns.current.set(index,performance.now()+3500)}});
      raceClock.current+=delta;
      if(checkpoints.length){
        const [checkpointX,checkpointZ]=checkpoints[nextCheckpoint.current];
        const inside=Math.hypot(kart.current.position.x-checkpointX,kart.current.position.z-checkpointZ)<1.8;
        if(inside&&!checkpointInside.current){
          if(nextCheckpoint.current===0&&lapStartedAt.current===null)lapStartedAt.current=raceClock.current;
          const advanced=nextCheckpoint.current+1;
          if(advanced>=checkpoints.length){
            const lapSeconds=raceClock.current-(lapStartedAt.current??raceClock.current);nextCheckpoint.current=0;lapStartedAt.current=null;lastReportedTenth.current=-1;onRaceEvent({nextCheckpoint:0,timeSeconds:0,lapSeconds});
          }else{nextCheckpoint.current=advanced;onRaceEvent({nextCheckpoint:advanced,timeSeconds:raceClock.current-(lapStartedAt.current??raceClock.current)})}
          checkpointInside.current=true;
        }else if(!inside)checkpointInside.current=false;
        if(lapStartedAt.current!==null){const elapsed=raceClock.current-lapStartedAt.current;const tenth=Math.floor(elapsed*10);if(tenth!==lastReportedTenth.current){lastReportedTenth.current=tenth;onRaceEvent({nextCheckpoint:nextCheckpoint.current,timeSeconds:elapsed})}}
      }
    }
    onSpeed(Math.round(Math.abs(speed.current)*11));
    onBoost(Math.round(boost.current));
  });
  return <group ref={kart} position={[spawn[0],.22,spawn[1]]}>
    <RoundedBox args={[1.02,.3,1.72]} radius={.13} smoothness={4} position={[0,.24,0]} castShadow><meshStandardMaterial color="#38d59d" metalness={.35} roughness={.28}/></RoundedBox>
    <RoundedBox args={[.76,.28,.78]} radius={.16} smoothness={4} position={[0,.48,-.12]} castShadow><meshStandardMaterial color="#10222c" metalness={.6} roughness={.18}/></RoundedBox>
    <mesh position={[0,.42,.6]} rotation={[Math.PI/2,0,0]} castShadow><coneGeometry args={[.43,.78,4]}/><meshStandardMaterial color="#49e3ae" metalness={.25} roughness={.3}/></mesh>
    <mesh position={[-.58,.22,0]} castShadow><boxGeometry args={[.18,.18,1.1]}/><meshStandardMaterial color="#18313a" metalness={.45} roughness={.3}/></mesh>
    <mesh position={[.58,.22,0]} castShadow><boxGeometry args={[.18,.18,1.1]}/><meshStandardMaterial color="#18313a" metalness={.45} roughness={.3}/></mesh>
    <mesh position={[0,.6,-.72]} castShadow><boxGeometry args={[1.15,.09,.28]}/><meshStandardMaterial color="#1a2b34" metalness={.55} roughness={.24}/></mesh>
    <mesh position={[-.42,.43,-.65]} castShadow><boxGeometry args={[.07,.35,.07]}/><meshStandardMaterial color="#17252c"/></mesh>
    <mesh position={[.42,.43,-.65]} castShadow><boxGeometry args={[.07,.35,.07]}/><meshStandardMaterial color="#17252c"/></mesh>
    <mesh position={[-.28,.36,.84]}><sphereGeometry args={[.1,12,8]}/><meshStandardMaterial color="#effffb" emissive="#a8ffe3" emissiveIntensity={2}/></mesh>
    <mesh position={[.28,.36,.84]}><sphereGeometry args={[.1,12,8]}/><meshStandardMaterial color="#effffb" emissive="#a8ffe3" emissiveIntensity={2}/></mesh>
    {([[-.54,.04,.5],[.54,.04,.5],[-.54,.04,-.5],[.54,.04,-.5]] as [number,number,number][]).map((position,index)=><mesh key={index} position={position} rotation={[0,0,Math.PI/2]} castShadow><cylinderGeometry args={[.21,.21,.18,20]}/><meshStandardMaterial color="#0a0e11" roughness={.82}/></mesh>)}
    <group ref={boostFlame} visible={false} position={[0,.27,-1.02]}>
      <mesh rotation={[-Math.PI/2,0,0]}><coneGeometry args={[.16,.85,12]}/><meshBasicMaterial color="#52ffd0" transparent opacity={.9}/></mesh>
      <pointLight color="#46e8ff" intensity={3} distance={2.5}/>
    </group>
  </group>
}

function BoostPads({pads}:{pads:[number,number][]}){return <>{pads.map(([x,z],index)=><group key={index} position={[x,.035,z]}>
  <mesh><cylinderGeometry args={[.72,.72,.06,24]}/><meshStandardMaterial color="#d89b35" emissive="#ffb52e" emissiveIntensity={1.2} metalness={.3} roughness={.4}/></mesh>
  <mesh rotation={[Math.PI/2,0,0]} position={[0,.05,0]}><torusGeometry args={[.46,.07,8,24]}/><meshBasicMaterial color="#fff1a3"/></mesh>
</group>)}</>}

function CheckpointGates({checkpoints,active}:{checkpoints:[number,number][];active:number}){return <>{checkpoints.map(([x,z],index)=>{
  const isActive=index===active;return <group key={index} position={[x,.06,z]}>
    <mesh rotation={[Math.PI/2,0,0]}><torusGeometry args={[1.55,.13,10,40]}/><meshStandardMaterial color={isActive?"#62ffd0":"#34545b"} emissive={isActive?"#32d9ae":"#14242a"} emissiveIntensity={isActive?2.2:.4}/></mesh>
    <mesh position={[-1.55,.65,0]}><cylinderGeometry args={[.09,.14,1.3,8]}/><meshStandardMaterial color={isActive?"#c9fff0":"#456168"} emissive={isActive?"#52f5ca":"#000000"}/></mesh>
    <mesh position={[1.55,.65,0]}><cylinderGeometry args={[.09,.14,1.3,8]}/><meshStandardMaterial color={isActive?"#c9fff0":"#456168"} emissive={isActive?"#52f5ca":"#000000"}/></mesh>
  </group>})}</>}

function SampleArena(){
  const wallMaterial=<meshStandardMaterial color="#1b3440" roughness={.82}/>;
  const accentMaterial=<meshStandardMaterial color="#246b59" roughness={.72}/>;
  return <group>
    <mesh position={[0,1,-14]} castShadow receiveShadow><boxGeometry args={[40,2,.2]}/>{wallMaterial}</mesh>
    <mesh position={[0,1,14]} castShadow receiveShadow><boxGeometry args={[40,2,.2]}/>{wallMaterial}</mesh>
    <mesh position={[-20,1,0]} castShadow receiveShadow><boxGeometry args={[.2,2,28]}/>{wallMaterial}</mesh>
    <mesh position={[20,1,0]} castShadow receiveShadow><boxGeometry args={[.2,2,28]}/>{wallMaterial}</mesh>
    <mesh position={[-11,.65,-7]} castShadow receiveShadow><boxGeometry args={[4,1.3,3]}/>{accentMaterial}</mesh>
    <mesh position={[11,.65,7]} castShadow receiveShadow><boxGeometry args={[4,1.3,3]}/>{accentMaterial}</mesh>
    <mesh position={[11,.35,-7]} rotation={[0,0,-.16]} castShadow receiveShadow><boxGeometry args={[4.5,.3,3]}/><meshStandardMaterial color="#416776" roughness={.75}/></mesh>
    <mesh position={[-11,.35,7]} rotation={[0,0,.16]} castShadow receiveShadow><boxGeometry args={[4.5,.3,3]}/><meshStandardMaterial color="#416776" roughness={.75}/></mesh>
    {([[-17,-11],[17,-11],[-17,11],[17,11]] as [number,number][]).map(([x,z],index)=><mesh key={index} position={[x,1.15,z]} castShadow receiveShadow><cylinderGeometry args={[.65,.9,2.3,6]}/><meshStandardMaterial color={index%2?"#325363":"#2d8b70"} roughness={.8}/></mesh>)}
    <mesh position={[0,.22,-10]} castShadow receiveShadow><boxGeometry args={[6,.44,.6]}/><meshStandardMaterial color="#d9904d" roughness={.8}/></mesh>
    <mesh position={[0,.22,10]} castShadow receiveShadow><boxGeometry args={[6,.44,.6]}/><meshStandardMaterial color="#d9904d" roughness={.8}/></mesh>
  </group>
}

function WorldScene({geometry,roomModel,onSpeed,onBoost,onPointerLock,onRaceEvent,activeCheckpoint,sample,config}:{geometry:THREE.BufferGeometry|null;roomModel:THREE.Object3D|null;onSpeed:(speed:number)=>void;onBoost:(boost:number)=>void;onPointerLock:(locked:boolean)=>void;onRaceEvent:(event:RaceEvent)=>void;activeCheckpoint:number;sample:boolean;config:WorldConfig}){
  const hasColors=geometry?.hasAttribute("color")??false;
  const floor:[number,number]=[config.bounds.maxX-config.bounds.minX,config.bounds.maxZ-config.bounds.minZ];
  const floorCenter:[number,number]=[(config.bounds.minX+config.bounds.maxX)/2,(config.bounds.minZ+config.bounds.maxZ)/2];
  return <>
    <color attach="background" args={["#071018"]}/>
    <fog attach="fog" args={["#071018",sample?38:14,sample?75:30]}/>
    <hemisphereLight args={["#d8fff3","#15202a",1.6]}/>
    <directionalLight position={[5,10,4]} intensity={2.4} castShadow shadow-mapSize={[2048,2048]}/>
    {geometry&&<mesh geometry={geometry} receiveShadow castShadow>
      <meshStandardMaterial vertexColors={hasColors} color={hasColors?"#ffffff":"#87aa9f"} roughness={.88} side={THREE.DoubleSide} transparent opacity={.72}/>
    </mesh>}
    {roomModel&&<primitive object={roomModel}/>} 
    <mesh rotation={[-Math.PI/2,0,0]} receiveShadow position={[floorCenter[0],-.025,floorCenter[1]]}><planeGeometry args={floor}/><meshStandardMaterial color="#0c1b22" roughness={1}/></mesh>
    <gridHelper args={[Math.max(...floor),sample?80:24,"#2b765f","#18303a"]} position={[floorCenter[0],0,floorCenter[1]]}/>
    {sample&&<SampleArena/>}
    <BoostPads pads={config.pads}/>
    <CheckpointGates checkpoints={config.checkpoints} active={activeCheckpoint}/>
    <Kart onSpeed={onSpeed} onBoost={onBoost} onPointerLock={onPointerLock} onRaceEvent={onRaceEvent} bounds={config.bounds} spawn={config.spawn} pads={config.pads} checkpoints={config.checkpoints} obstacles={config.obstacles}/>
  </>
}

function normalizeGeometry(source:THREE.BufferGeometry,navigation?:NavigationMetadata){
  const geometry=source.clone();
  if(navigation){
    const position=geometry.getAttribute("position");const transform=navigation.coordinate_system;
    const basisX=new THREE.Vector3(...transform.basis_x);const basisY=new THREE.Vector3(...transform.basis_y);const basisZ=new THREE.Vector3(...transform.basis_z);
    for(let index=0;index<position.count;index++){
      const delta=new THREE.Vector3(position.getX(index)-transform.origin[0],position.getY(index)-transform.origin[1],position.getZ(index)-transform.origin[2]);
      const projectedX=delta.dot(basisX);const projectedY=delta.dot(basisY);const projectedZ=delta.dot(basisZ);
      position.setXYZ(index,(projectedX-transform.center_x)*transform.scale,projectedY*transform.scale,(projectedZ-transform.center_z)*transform.scale);
    }
    position.needsUpdate=true;geometry.computeVertexNormals();geometry.computeBoundingBox();geometry.computeBoundingSphere();return geometry;
  }
  geometry.computeVertexNormals();geometry.computeBoundingBox();
  const box=geometry.boundingBox!;const center=box.getCenter(new THREE.Vector3());
  geometry.translate(-center.x,-box.min.y,-center.z);geometry.computeBoundingBox();
  const size=geometry.boundingBox!.getSize(new THREE.Vector3());
  const scale=10/Math.max(size.x,size.z,1);geometry.scale(scale,scale,scale);
  geometry.computeBoundingSphere();return geometry;
}

function sampleGeometry(){const geometry=new THREE.PlaneGeometry(40,28,20,14);geometry.rotateX(-Math.PI/2);return geometry}

export function ReconstructedWorld({captureId,desktopToken,sample=false,onExit}:Props){
  const [geometry,setGeometry]=useState<THREE.BufferGeometry|null>(()=>sample?sampleGeometry():null);
  const [roomModel,setRoomModel]=useState<THREE.Object3D|null>(null);
  const [navigation,setNavigation]=useState<NavigationMetadata|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [speed,setSpeed]=useState(0);
  const [boost,setBoost]=useState(100);
  const [mouseCaptured,setMouseCaptured]=useState(false);
  const [showControls,setShowControls]=useState(false);
  const [race,setRace]=useState<{nextCheckpoint:number;lap:number;timeSeconds:number;bestSeconds:number|null}>({nextCheckpoint:0,lap:1,timeSeconds:0,bestSeconds:null});
  const handleRaceEvent=useCallback((event:RaceEvent)=>setRace((current)=>{
    if(event.reset)return{nextCheckpoint:0,lap:1,timeSeconds:0,bestSeconds:current.bestSeconds};
    if(event.lapSeconds!==undefined)return{nextCheckpoint:0,lap:current.lap+1,timeSeconds:0,bestSeconds:current.bestSeconds===null?event.lapSeconds:Math.min(current.bestSeconds,event.lapSeconds)};
    return{...current,nextCheckpoint:event.nextCheckpoint??current.nextCheckpoint,timeSeconds:event.timeSeconds??current.timeSeconds};
  }),[]);
  useEffect(()=>{if(sample)return;let active=true;if(!captureId||!desktopToken){setError("Capture credentials are missing");return}Promise.all([getReconstructionModel(captureId,desktopToken),getReconstructionNavigation(captureId,desktopToken)]).then(([buffer,map])=>{if(!active)return;const manager=new THREE.LoadingManager();manager.setURLModifier((url)=>`/api/capture-sessions/${captureId}/reconstruction/assets/${encodeURIComponent(url.split("/").pop()??url)}?token=${encodeURIComponent(desktopToken)}`);new GLTFLoader(manager).parse(buffer,"",(gltf)=>{if(!active)return;gltf.scene.traverse((object)=>{if(object instanceof THREE.Mesh){object.geometry=normalizeGeometry(object.geometry,map);object.castShadow=true;object.receiveShadow=true}});setNavigation(map);setRoomModel(gltf.scene)},(reason)=>{if(active)setError(reason instanceof Error?reason.message:"Could not parse textured room")})},(reason)=>{if(active)setError(reason instanceof Error?reason.message:"Could not load textured room")});return()=>{active=false}},[captureId,desktopToken,sample]);
  useEffect(()=>()=>geometry?.dispose(),[geometry]);
  const dialStyle={"--boost-angle":`${boost*3.6}deg`} as CSSProperties;
  const resumeGame=()=>{setShowControls(false);window.dispatchEvent(new Event("gaussiancircuit:resume"))};
  const restartGame=()=>{window.dispatchEvent(new Event("gaussiancircuit:restart"));resumeGame()};
  const config:WorldConfig=sample
    ?{bounds:{minX:-19.4,maxX:19.4,minZ:-13.4,maxZ:13.4},spawn:[0,0],pads:SAMPLE_BOOST_PADS,checkpoints:SAMPLE_CHECKPOINTS,obstacles:SAMPLE_OBSTACLES}
    :navigation
      ?{bounds:{minX:navigation.bounds.center_x-navigation.bounds.half_x+.6,maxX:navigation.bounds.center_x+navigation.bounds.half_x-.6,minZ:navigation.bounds.center_z-navigation.bounds.half_z+.6,maxZ:navigation.bounds.center_z+navigation.bounds.half_z-.6},spawn:navigation.spawn,pads:navigation.boost_pads,checkpoints:navigation.checkpoints,obstacles:navigation.obstacles}
      :{bounds:{minX:-5.6,maxX:5.6,minZ:-4.2,maxZ:4.2},spawn:[0,0],pads:[],checkpoints:[],obstacles:undefined};
  return <main className="world-shell">
    <header><div className="brand"><i/>Gaussian<span>Circuit</span></div></header>
    <section className="world-stage">
      {geometry||roomModel?<Canvas shadows camera={{position:[0,2.7,-4.7],fov:48,near:.05,far:120}}><WorldScene geometry={geometry} roomModel={roomModel} onSpeed={setSpeed} onBoost={setBoost} onPointerLock={setMouseCaptured} onRaceEvent={handleRaceEvent} activeCheckpoint={race.nextCheckpoint} sample={sample} config={config}/></Canvas>:<div className="world-loading">{error?<><strong>Room could not be opened</strong><span>{error}</span></>:<>Loading reconstructed room…</>}</div>}
      <div className={mouseCaptured?"drive-dial":"drive-dial paused"} style={dialStyle}><div><small>{mouseCaptured?"KM/H":"PAUSED"}</small><strong>{speed}</strong><span>BOOST {boost}</span></div></div>
      {config.checkpoints.length>0&&<div className="race-status"><span>LAP <b>{race.lap}</b></span><span>GATE <b>{race.nextCheckpoint+1}/{config.checkpoints.length}</b></span><span>TIME <b>{race.timeSeconds.toFixed(1)}</b></span><span>BEST <b>{race.bestSeconds===null?"--":race.bestSeconds.toFixed(1)}</b></span></div>}
      {!mouseCaptured&&<div className="pause-layer"><div className="pause-card"><p>GAUSSIANCIRCUIT</p><h2>PAUSED</h2>{showControls?<><div className="control-list"><span><b>WASD / ARROWS</b> Drive and steer</span><span><b>HOLD CLICK</b> Boost</span><span><b>SPACE</b> Brake</span><span><b>R</b> Reset kart</span><span><b>ESC</b> Pause</span></div><button className="pause-action primary-action" onClick={()=>setShowControls(false)}>Back</button></>:<><button className="pause-action primary-action" onClick={resumeGame}>Resume Game</button><button className="pause-action" onClick={restartGame}>Restart Run</button><button className="pause-action" onClick={()=>setShowControls(true)}>How to Drive</button><button className="pause-action danger-action" onClick={onExit}>Exit Room</button></>}</div></div>}
    </section>
  </main>
}
