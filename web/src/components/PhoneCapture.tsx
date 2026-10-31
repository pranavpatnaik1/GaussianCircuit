import React, { ChangeEvent, useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { finishCapture, joinCapture, uploadPhotos, uploadScanVideo } from "../api";
import type { JoinedCapture } from "../types";

interface QueuedPhoto { id: string; file: File }
const queueId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

function GuidedScanner({joined,onComplete,onUsePhotos}:{joined:JoinedCapture;onComplete:()=>void;onUsePhotos:()=>void}){
  const videoRef=useRef<HTMLVideoElement>(null);
  const streamRef=useRef<MediaStream|null>(null);
  const recorderRef=useRef<MediaRecorder|null>(null);
  const chunksRef=useRef<Blob[]>([]);
  const originHeading=useRef<number|null>(null);
  const orientationSeen=useRef(false);
  const [phase,setPhase]=useState<"ready"|"recording"|"review">("ready");
  const [elapsed,setElapsed]=useState(0);
  const [coverage,setCoverage]=useState(()=>Array(12).fill(false) as boolean[]);
  const [file,setFile]=useState<File|null>(null);
  const [cameraError,setCameraError]=useState<string|null>(null);
  const upload=useMutation({mutationFn:async(video:File)=>{const result=await uploadScanVideo(joined.id,joined.phone_token,video);await finishCapture(joined.id,joined.phone_token);return result},onSuccess:onComplete});
  useEffect(()=>()=>streamRef.current?.getTracks().forEach((track)=>track.stop()),[]);
  useEffect(()=>{
    if(phase!=="recording")return;
    const timer=window.setInterval(()=>setElapsed((seconds)=>seconds+1),1000);
    const orient=(event:DeviceOrientationEvent)=>{if(event.alpha===null)return;orientationSeen.current=true;if(originHeading.current===null)originHeading.current=event.alpha;const relative=(event.alpha-originHeading.current+360)%360;const sector=Math.min(11,Math.floor(relative/30));setCoverage((current)=>current.map((value,index)=>value||index===sector))};
    window.addEventListener("deviceorientation",orient);
    return()=>{window.clearInterval(timer);window.removeEventListener("deviceorientation",orient)};
  },[phase]);
  useEffect(()=>{if(phase==="recording"&&!orientationSeen.current)setCoverage((current)=>current.map((value,index)=>value||index===Math.min(11,Math.floor(elapsed/4))))},[elapsed,phase]);
  const begin=async()=>{
    setCameraError(null);
    try{
      const orientation=DeviceOrientationEvent as typeof DeviceOrientationEvent&{requestPermission?:()=>Promise<"granted"|"denied">};
      if(orientation.requestPermission)await orientation.requestPermission();
      const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"},width:{ideal:1920},height:{ideal:1080}},audio:false});
      streamRef.current=stream;if(videoRef.current){videoRef.current.srcObject=stream;await videoRef.current.play()}
      const types=["video/mp4;codecs=h264","video/mp4","video/webm;codecs=vp9","video/webm"];
      const mimeType=types.find((type)=>MediaRecorder.isTypeSupported(type))??"";
      const recorder=new MediaRecorder(stream,mimeType?{mimeType,videoBitsPerSecond:8_000_000}:undefined);
      recorderRef.current=recorder;chunksRef.current=[];setElapsed(0);setCoverage(Array(12).fill(false));originHeading.current=null;orientationSeen.current=false;
      recorder.ondataavailable=(event)=>{if(event.data.size)chunksRef.current.push(event.data)};
      recorder.onstop=()=>{const type=recorder.mimeType||"video/mp4";const extension=type.includes("webm")?"webm":"mp4";setFile(new File(chunksRef.current,`room-scan.${extension}`,{type}));stream.getTracks().forEach((track)=>track.stop());setPhase("review")};
      recorder.start(1000);setPhase("recording");
    }catch{setCameraError("Live scanning needs camera permission and HTTPS. You can still record with the iPhone camera below.")}
  };
  const stop=()=>{if(recorderRef.current?.state==="recording")recorderRef.current.stop()};
  const chooseVideo=(event:ChangeEvent<HTMLInputElement>)=>{const selected=event.target.files?.[0];if(selected){setFile(selected);setPhase("review")}event.target.value=""};
  const covered=coverage.filter(Boolean).length;
  const instruction=covered<3?"Start at a corner and walk slowly":covered<8?"Good—keep the last view partly visible":covered<11?"Continue around the room perimeter":"Coverage loop complete";
  if(phase==="review"&&file)return <main className="phone scan-phone"><header><div className="brand"><i/>GC</div><span className="pill">SCAN READY</span></header><section className="scan-review"><div className="scan-complete">✓</div><p className="eyebrow">ROOM WALK RECORDED</p><h1>Extract the best viewpoints?</h1><p className="muted">GaussianCircuit will discard blur and duplicates, keep the strongest overlapping frames, then begin local reconstruction.</p><div className="scan-summary"><span><b>{elapsed||"—"}</b> seconds</span><span><b>{covered||"—"}</b> coverage sectors</span><span><b>{Math.max(1,Math.round(file.size/1048576))}</b> MB</span></div><button className="primary wide" onClick={()=>upload.mutate(file)} disabled={upload.isPending}>{upload.isPending?"Extracting frames and uploading…":"Use this room scan"}</button><button className="text-button wide" onClick={()=>{setFile(null);setPhase("ready")}} disabled={upload.isPending}>Record again</button>{upload.error&&<p className="error">{upload.error.message}</p>}</section></main>;
  return <main className="phone scan-phone"><header><div className="brand"><i/>GC</div><span className="pill">{phase==="recording"?`${elapsed}s`:`${covered}/12`}</span></header><section className="scan-intro"><p className="eyebrow">GUIDED ROOM SCAN</p><h1>{phase==="recording"?instruction:"Walk one slow loop."}</h1><p className="muted">Like Face ID setup, the ring fills as you cover the room. Keep the floor and furniture visible and move—don’t stand still and pan.</p></section><div className={`scan-viewport ${phase==="recording"?"is-recording":""}`}><video ref={videoRef} muted playsInline/>{Array.from({length:12},(_,index)=><i key={index} className={coverage[index]?"covered":index===covered?"current":""} style={{"--sector":index} as React.CSSProperties}/>)}<div className="scan-reticle"><span/><span/><span/><span/></div><div className="scan-instruction"><b>{phase==="recording"?instruction:"30–60 seconds"}</b><small>{phase==="recording"?(orientationSeen.current?"Live orientation coverage":"Paced coverage estimate"):"Walk around the perimeter"}</small></div></div>{phase==="recording"?<button className="scan-stop" onClick={stop} disabled={elapsed<20}><i/> {elapsed<20?`Keep scanning ${20-elapsed}s`:"Finish scan"}</button>:<button className="primary wide scan-start" onClick={begin}>Start guided scan</button>}{cameraError&&<p className="error">{cameraError}</p>}<label className="secondary wide video-fallback">Record with iPhone Camera<input type="file" accept="video/mp4,video/quicktime,video/webm" capture="environment" onChange={chooseVideo}/></label><button className="text-button wide" onClick={onUsePhotos}>Use individual photos instead</button></main>;
}

export function PhoneCapture() {
  const [code, setCode] = useState(new URLSearchParams(location.search).get("code")?.toUpperCase() ?? "");
  const [joined, setJoined] = useState<JoinedCapture | null>(null);
  const [queue, setQueue] = useState<QueuedPhoto[]>([]);
  const [count, setCount] = useState(0);
  const [done, setDone] = useState(false);
  const [photoFallback,setPhotoFallback]=useState(false);
  const join = useMutation({ mutationFn: () => joinCapture(code), onSuccess: setJoined });
  const upload = useMutation({
    mutationFn: () => uploadPhotos(joined!.id, joined!.phone_token, queue.map(({ file }) => file)),
    onSuccess: (response) => { setCount(response.snapshot.photo_count); setQueue([]); },
  });
  const finish = useMutation({ mutationFn: () => finishCapture(joined!.id, joined!.phone_token), onSuccess: () => setDone(true) });
  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    const additions = Array.from(event.target.files ?? []).map((file) => ({ id: queueId(), file }));
    setQueue((current) => [...current, ...additions]);
    // iOS often reuses the filename; reset the input so every Use Photo fires.
    event.target.value = "";
  };
  if (done) return <main className="phone centered"><div className="success">✓</div><p className="eyebrow">CAPTURE COMPLETE</p><h1>Head back to your computer.</h1><p className="muted">Your photos arrived safely.</p></main>;
  if (!joined) return <main className="phone centered"><div className="brand phone-brand"><i/>Gaussian<span>Circuit</span></div><p className="eyebrow">JOIN ROOM CAPTURE</p><h1>Enter the code on your computer.</h1><input className="code-input" value={code} onChange={(event)=>setCode(event.target.value.toUpperCase())} placeholder="SJ8-K19" autoCapitalize="characters" autoCorrect="off" maxLength={7}/><button className="primary wide" onClick={()=>join.mutate()} disabled={join.isPending||code.length<6}>{join.isPending?"Connecting…":"Join capture"}</button>{join.error&&<p className="error">{join.error.message}</p>}<small>The code expires automatically and only connects to this room session.</small></main>;
  if(!photoFallback)return <GuidedScanner joined={joined} onComplete={()=>setDone(true)} onUsePhotos={()=>setPhotoFallback(true)}/>;
  return <main className="phone">
    <header><div className="brand"><i/>GC</div><span className="pill">{count} / {joined.minimum_images} uploaded</span></header>
    <section><p className="eyebrow">CAPTURE YOUR ROOM</p><h1>Walk it—do not stand and pan.</h1><p className="muted">Take one small step between photos. Keep about 70% of the previous view visible and complete a slow loop around the room.</p><p className="notice">Aim for 30–50 photos. Keep the floor, walls, and furniture together in frame.</p></section>
    <label className="camera"><b>◎</b><strong>{queue.length ? "Take another overlapping photo" : "Take first photo"}</strong><small>Rear camera • {queue.length} queued</small><input type="file" accept="image/jpeg,image/png,image/heic,image/heif" capture="environment" multiple onChange={choose}/></label>
    {queue.length>0&&<section className="selection"><div><strong>{queue.length} photo{queue.length===1?"":"s"} queued</strong><button className="text-button" onClick={()=>setQueue([])}>Clear all</button></div><div className="file-list">{queue.map(({id,file},index)=><span key={id}><b>Photo {index+1}</b><small>{Math.max(1,Math.round(file.size/1024))} KB</small><button type="button" aria-label={`Remove photo ${index+1}`} onClick={()=>setQueue((current)=>current.filter((item)=>item.id!==id))}>×</button></span>)}</div><button className="primary wide" onClick={()=>upload.mutate()} disabled={upload.isPending}>{upload.isPending?"Uploading batch…":`Upload ${queue.length} photo${queue.length===1?"":"s"}`}</button></section>}
    {upload.data&&<p className="notice">Uploaded {upload.data.accepted.length} new photos{upload.data.duplicate_count?`; skipped ${upload.data.duplicate_count} duplicates`:""}.</p>}{upload.error&&<p className="error">{upload.error.message}</p>}
    <section className="finish"><div><strong>{count>=joined.minimum_images?"Minimum reached—add more if coverage has gaps":`${joined.minimum_images-count} more uploaded photos needed`}</strong><span>Finish only after one complete overlapping loop.</span></div><button className="secondary" onClick={()=>finish.mutate()} disabled={count<joined.minimum_images||finish.isPending||queue.length>0}>Finish capture</button></section>
    {queue.length>0&&count>=joined.minimum_images&&<p className="notice">Upload or clear the queue before finishing.</p>}{finish.error&&<p className="error">{finish.error.message}</p>}
  </main>;
}
