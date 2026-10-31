import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

interface DesktopSession { id:string; code:string; desktopToken:string; expiresAt:string; captureUrl?:string }
interface AppState { desktopSession:DesktopSession|null; setDesktopSession:(session:DesktopSession)=>void; clearDesktopSession:()=>void }
export const useAppStore=create<AppState>()(persist((set)=>({
  desktopSession:null,
  setDesktopSession:(desktopSession)=>set({desktopSession}),
  clearDesktopSession:()=>set({desktopSession:null}),
}),{name:"gaussiancircuit-desktop-session",version:1,storage:createJSONStorage(()=>sessionStorage)}));
