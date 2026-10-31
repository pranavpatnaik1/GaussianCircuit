import type { CaptureSnapshot, CreatedCapture, JoinedCapture, NavigationMetadata, ReconstructionStatus, UploadResponse } from "./types";

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(payload.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
export async function createCapture(): Promise<CreatedCapture> {
  return parse(await fetch("/api/capture-sessions", { method: "POST" }));
}
export async function joinCapture(code: string): Promise<JoinedCapture> {
  return parse(await fetch("/api/capture-sessions/join", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({code}) }));
}
export async function getCapture(id:string, token:string):Promise<CaptureSnapshot> {
  return parse(await fetch(`/api/capture-sessions/${id}`, {headers:{Authorization:`Bearer ${token}`}}));
}
export async function uploadPhotos(id:string, token:string, files:File[]):Promise<UploadResponse> {
  const body=new FormData(); files.forEach((file)=>body.append("files",file));
  return parse(await fetch(`/api/capture-sessions/${id}/photos`,{method:"POST",headers:{Authorization:`Bearer ${token}`},body}));
}
export async function uploadScanVideo(id:string,token:string,file:File):Promise<UploadResponse> {
  const body=new FormData();body.append("video",file);
  return parse(await fetch(`/api/capture-sessions/${id}/video`,{method:"POST",headers:{Authorization:`Bearer ${token}`},body}));
}
export async function finishCapture(id:string,token:string):Promise<CaptureSnapshot> {
  return parse(await fetch(`/api/capture-sessions/${id}/finish`,{method:"POST",headers:{Authorization:`Bearer ${token}`}}));
}
export async function getReconstruction(id:string,token:string):Promise<ReconstructionStatus> {
  return parse(await fetch(`/api/capture-sessions/${id}/reconstruction`,{headers:{Authorization:`Bearer ${token}`}}));
}
export async function getReconstructionMesh(id:string,token:string):Promise<ArrayBuffer> {
  const response=await fetch(`/api/capture-sessions/${id}/reconstruction/mesh`,{headers:{Authorization:`Bearer ${token}`}});
  if(!response.ok){
    const payload=await response.json().catch(()=>({detail:"Could not load room mesh"}));
    throw new Error(payload.detail??`Could not load room mesh (${response.status})`);
  }
  return response.arrayBuffer();
}
export async function getReconstructionNavigation(id:string,token:string):Promise<NavigationMetadata> {
  return parse(await fetch(`/api/capture-sessions/${id}/reconstruction/navigation`,{headers:{Authorization:`Bearer ${token}`}}));
}
export async function getReconstructionModel(id:string,token:string):Promise<ArrayBuffer> {
  const response=await fetch(`/api/capture-sessions/${id}/reconstruction/model`,{headers:{Authorization:`Bearer ${token}`}});
  if(!response.ok){const payload=await response.json().catch(()=>({detail:"Could not load textured room"}));throw new Error(payload.detail??`Could not load textured room (${response.status})`)}
  return response.arrayBuffer();
}
