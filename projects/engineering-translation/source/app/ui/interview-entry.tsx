"use client";
import { useEffect, useState } from "react";
import { api } from "./api";
let pending: Promise<unknown> | undefined;
export default function InterviewEntry() {
  const [error,setError]=useState("");
  useEffect(()=>{
    pending ??= api("interview/session",{method:"POST",body:"{}"});
    void pending.then(()=>window.location.reload()).catch(e=>{pending=undefined;setError(e.message)});
  },[]);
  return <main className="surface" style={{maxWidth:640,margin:"12vh auto",padding:32}}><h1>工程文件翻译</h1><p aria-live="polite">{error || "正在打开你的文件工作台…"}</p>{error && <button onClick={()=>location.reload()}>重试</button>}</main>;
}
