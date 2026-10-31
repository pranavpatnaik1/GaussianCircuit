import { OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";

function Room(){return <group>
  <mesh rotation={[-Math.PI/2,0,0]} receiveShadow><planeGeometry args={[8,6]}/><meshStandardMaterial color="#cedbd7" roughness={.9}/></mesh>
  <mesh position={[0,.55,0]} castShadow><boxGeometry args={[2,1.1,1.7]}/><meshStandardMaterial color="#324153" roughness={.6}/></mesh>
  <mesh position={[0,.035,0]} rotation={[-Math.PI/2,0,0]}><torusGeometry args={[2.45,.22,12,96,Math.PI*2]}/><meshStandardMaterial color="#15936d" roughness={.75}/></mesh>
</group>}
export function ScenePreview(){return <div className="scene-preview" aria-label="Example reconstructed room and circuit">
  <Canvas shadows camera={{position:[6.5,6,7],fov:40}}><color attach="background" args={["#0c141d"]}/><ambientLight intensity={1.3}/><directionalLight position={[4,8,5]} intensity={2.5} castShadow/><Room/><gridHelper args={[12,24,"#294251","#172a35"]} position={[0,-.015,0]}/><OrbitControls makeDefault maxPolarAngle={Math.PI/2.05}/></Canvas>
  <div className="scene-label"><span/> Example planning scene</div>
</div>}
