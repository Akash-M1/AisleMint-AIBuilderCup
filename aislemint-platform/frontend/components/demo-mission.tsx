'use client';
import Link from 'next/link';
import { useState } from 'react';
import { ArrowRight, CheckCircle2, Copy, Play, RotateCcw, Sparkles } from 'lucide-react';
import { api } from '@/lib/api';
import './demo-mission.css';
const flyer = 'FICTIONAL COMPETITOR FLYER — Weekend Match Day: 25% OFF Honey Oats 500g; 20% OFF Mango Juice 1L; 15% OFF Masala Chips 100g. Offers valid this weekend. Simulated offers for AI Builder Cup.';
const steps = [
 {n:'01',title:'See the risk',path:'/connect',label:'Connected intelligence',detail:'Oats and juice are below reorder point. Follow products to suppliers and overdue purchase orders.',prompt:'Which products have low stock and overdue deliveries? Cite the evidence.'},
 {n:'02',title:'Read the rival’s flyer',path:'/promotions/scenarios',label:'Scenario studio',detail:'Scan the supplied fictional flyer. Gemini extracts offers; catalog checks reject unknown SKUs.',prompt:flyer},
 {n:'03',title:'Protect the margin',path:'/promotions/scenarios',label:'Simulate & review',detail:'Compare unfunded and proposed funded results. Inspect assumptions, then confirm a reviewed promotion.',prompt:'Use Honey Oats (MINT-OATS), 15% discount, one week. Review the computed result before confirming.'},
 {n:'04',title:'Plan the shelf',path:'/demand/inventory',label:'Demand & inventory',detail:'Forecast demand and inspect reorder proposals against the same synthetic catalog and stock evidence.',prompt:'Select the Bengaluru store and inspect Honey Oats stock and replenishment assumptions.'},
 {n:'05',title:'Build a basket',path:'/sales/pos',label:'OneSales AI shopper',detail:'Ask for a ₹500 match-day basket, then load it into checkout. Stock, GST and promotions are checked in code.',prompt:'Build a match-day snack basket with juice and chips under ₹500. Prefer active offers and stay within stock.'},
 {n:'06',title:'Follow the evidence',path:'/sales/analytics',label:'Business analytics',detail:'Explore synthetic sales and contribution, then inspect data lineage and evaluated models.',prompt:'These are simulated outcomes and retrospective tests, not measured customer ROI.'},
];
export function DemoBanner(){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 return <div className="cup-banner"><Link href="/demo"><Sparkles size={16}/> AI BUILDER CUP · INTERACTIVE SIMULATION</Link><span>Your own sandbox · synthetic data · no login</span><button disabled={busy} onClick={async()=>{setBusy(true);setError('');try{await api('/demo/reset','',{});location.href='/demo';}catch(e:any){setError(e.message);setBusy(false);}}}><RotateCcw size={14}/>{busy?'Preparing…':'Start fresh'}</button>{error&&<small role="alert">{error}</small>}</div>
}
export default function DemoMission(){
 const [copied,setCopied]=useState('');
 async function copy(value:string){try{await navigator.clipboard.writeText(value);setCopied(value);}catch{setCopied('');}}
 return <section className="cup-mission">
  <div className="cup-hero"><div><p className="cup-eyebrow">AISLEMINT / AI BUILDER CUP 2026</p><h1>A rival drops prices.<br/><em>What’s your next move?</em></h1><p className="cup-intro">Step into MintMart, a fictional Bengaluru grocer. Turn a competitor flyer into a reviewed promotion, a replenishment decision and a basket that adds up.</p><div className="cup-actions"><Link className="cup-primary" href="/connect"><Play size={16}/> Start the guided mission</Link><a href="/demo-flyer.png" target="_blank" rel="noreferrer">Open sample flyer <ArrowRight size={15}/></a></div><p className="cup-disclosure">Every trading record here is synthetic. Gemini outputs are labelled; financial projections are estimates. No real payments, emails or supplier orders are sent.</p></div><div className="cup-decision"><span>YOUR MISSION</span><h2>Save the match-day basket.</h2><div><b>8</b><span>packs of oats on shelf<br/>reorder point: 25</span></div><div><b>25%</b><span>fictional competitor<br/>oats discount</span></div><div><b>₹500</b><span>shopper budget<br/>checked at checkout</span></div><footer><CheckCircle2 size={17}/> AI proposes. Retail engines calculate. You review.</footer></div></div>
  <div className="cup-stats"><span><b>12</b> connected SKUs</span><span><b>156 weeks</b> simulated promotion history</span><span><b>1,092 days</b> simulated demand history</span><span><b>5 areas</b> one retail workflow</span></div>
  <div className="cup-section-heading"><div><p className="cup-eyebrow">EXPLORE IN SIX MOVES</p><h2>From a signal to a store decision.</h2></div><p>Follow the story or open any product area. Your changes stay in this browser’s separate workspace.</p></div>
  <div className="cup-steps">{steps.map(step=><article key={step.n}><span className="cup-step-number">{step.n}</span><small>{step.label}</small><h3>{step.title}</h3><p>{step.detail}</p><div className="cup-prompt">{step.prompt}</div><div className="cup-step-actions"><Link href={step.path}>Explore <ArrowRight size={16}/></Link><button onClick={()=>copy(step.prompt)}><Copy size={14}/>{copied===step.prompt?'Copied':'Copy prompt'}</button></div></article>)}</div>
  <div className="cup-roles"><div><h3>What Gemini does</h3><p>Interprets flyer text and images, proposes catalog items, calls read-only tools and explains evidence.</p></div><div><h3>What code verifies</h3><p>Workspace scope, catalog identity, stock, budget, promotions, GST and evidence citation bounds.</p></div><div><h3>What you decide</h3><p>Review forecast assumptions, confirm a promotion and choose how the synthetic store should respond.</p></div></div>
  <div className="cup-actions"><Link href="/data/models">Model evaluation & activation →</Link><Link href="/data/connections">Data connections & lineage →</Link><Link href="/promotions/recommendations">Promotion optimizer →</Link><Link href="/sales/returns">Sales & returns →</Link></div><p className="cup-source"><a href="https://github.com/Akash-M1/AisleMint-AIBuilderCup" target="_blank" rel="noreferrer">Explore the source and reproducible simulation →</a></p>
 </section>
}
