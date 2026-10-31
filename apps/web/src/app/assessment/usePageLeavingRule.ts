"use client";
import {useCallback,useEffect,useRef,useState,MutableRefObject} from 'react';

// Visibility, not window blur: opening hints or clicking the editor never counts.
export function usePageLeavingRule(token:string,enabled:boolean,deadline:number,offset:MutableRefObject<number>,recorded:boolean){
 const [leftPage,setLeftPage]=useState(recorded);
 const pending=useRef(false),sending=useRef<Promise<void>|null>(null),acknowledged=useRef(recorded);
 const key='proofstack-hidden-'+token;
 const endpoint='/api/portfolio/sessions/'+token+'/page-hidden';
 const flush=useCallback(async()=>{
  if(!pending.current)return;
  if(sending.current)return sending.current;
  sending.current=(async()=>{
   const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',keepalive:true,signal:AbortSignal.timeout(110000)});
   if(!response.ok)throw Error('Could not sync the page-leaving event. Reconnect and retry submission.');
   const result=await response.json();
   acknowledged.current=!!result.recorded;pending.current=false;
   try{localStorage.removeItem(key);}catch{}
  })().finally(()=>{sending.current=null;});
  return sending.current;
 },[endpoint,key]);
 useEffect(()=>{if(recorded){acknowledged.current=true;setLeftPage(true);}},[recorded]);
 useEffect(()=>{
  if(!enabled)return;
  try{if(localStorage.getItem(key)){pending.current=true;setLeftPage(true);}}catch{}
  function sync(){void flush().catch(()=>{/* Persisted locally and retried; submission also waits. */});}
  function visibility(){
   if(document.visibilityState==='hidden'&&!acknowledged.current&&Date.now()/1000+offset.current<deadline){
    pending.current=true;setLeftPage(true);
    try{localStorage.setItem(key,'1');}catch{}
    // Beacon survives navigation. Keep pending until an acknowledged retry succeeds.
    try{navigator.sendBeacon(endpoint,new Blob(['{}'],{type:'application/json'}));}catch{}
   }
   sync();
  }
  document.addEventListener('visibilitychange',visibility);window.addEventListener('online',sync);
  visibility();const retry=setInterval(sync,5000);
  return()=>{document.removeEventListener('visibilitychange',visibility);window.removeEventListener('online',sync);clearInterval(retry);};
 },[enabled,deadline,endpoint,key,flush,offset]);
 return {leftPage,flush};
}
