import React from "react"; import ReactDOM from "react-dom/client"; import { QueryClient,QueryClientProvider } from "@tanstack/react-query"; import App from "./App"; import "./styles.css"; import "./world.css"; import "./pointer-lock.css"; import "./drive-dial.css"; import "./race.css"; import "./scan.css";
const client=new QueryClient({defaultOptions:{queries:{staleTime:1000,refetchOnWindowFocus:true}}});
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><QueryClientProvider client={client}><App/></QueryClientProvider></React.StrictMode>);
