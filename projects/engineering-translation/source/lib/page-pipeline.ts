import { randomUUID, createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { setTimeout as delay } from "node:timers/promises";
import { mkdir, readFile, writeFile, rename, copyFile, cp, access } from "node:fs/promises";
import path from "node:path";
import sharp, { type Sharp } from "sharp";
import { z } from "zod";
import { db, dataRoot, dailyModelLimit } from "./db";
import { AppError, rateLimit } from "./request-limits";
import { modelJSON, providerStatus } from "./models";
import { checkTranslation } from "./checks";
import { identifyFile } from "./file-profile";
import { checkCompleteness, checkVisualCoverage, type LayoutEntry } from "./page-completeness";
import { pageTranslationPrompt, pageReviewPrompt, pageRepairPrompt } from "./page-prompts";
import type { PipelineJob, PageProgress, PageIssue, PageStage } from "./page-pipeline-types";
import type { Provider } from "./types";

const runFile = promisify(execFile);
const python = () => process.env.CAD_OCR_PYTHON || (process.platform === "win32"
  ? path.join(process.cwd(), ".venv-ocr", "Scripts", "python.exe") : "/opt/cad-ocr/bin/python");
const workerScript = () => path.join(process.cwd(), "scripts", "page-worker.py");
export async function pageWorker(args: string[]) {
  const { stdout } = await runFile(python(), [workerScript(), ...args], { timeout: 180_000, maxBuffer: 4 * 1024 * 1024 });
  return JSON.parse(stdout.trim().split(/\r?\n/).at(-1)!) as Record<string, unknown>;
}

type Row = { id: string; source: string; target: string; box: number[]; disposition: "translate" | "skip" | "uncertain";
  fontSize?: number; angle?: number; origin?: string; confidence?: number; reason?: string; sourceBoxes?: number[][]; layoutBox?: number[];
  fontScale?: number; mergedIds?: string[]; sourceQuads?: number[][][] };
const boxSchema = z.tuple([z.number().min(0).max(1), z.number().min(0).max(1), z.number().positive().max(1), z.number().positive().max(1)]);
type Prepared = { page: number; width: number; height: number; kind: string; ocrUsed: boolean; rows: Row[]; sheets?: string[] };
const itemSchema = z.object({ id: z.string(), t: z.string().max(5000).optional(), s: z.string().max(5000).optional(),
  disposition: z.enum(["translate", "skip", "uncertain"]).default("translate"), reason: z.string().max(1500).optional(), layoutBox: boxSchema.optional(),
  angle: z.number().min(-180).max(180).optional(), fontScale: z.number().min(.55).max(1.15).optional() });
const operationSchema = z.object({ kind: z.enum(["merge", "mask"]), ids: z.array(z.string()).min(1).max(12),
  evidence: z.string().min(1).max(1500), t: z.string().max(5000).optional(), boxes: z.array(boxSchema).max(12).optional() });
const extraSchema = z.object({ s: z.string().min(1).max(5000), t: z.string().min(1).max(5000),
  angle: z.number().min(-180).max(180).optional(), fontScale: z.number().min(.55).max(1.15).optional(),
  box: z.tuple([z.number().min(0).max(1), z.number().min(0).max(1), z.number().positive().max(1), z.number().positive().max(1)]) });
const translationSchema = z.object({ items: z.array(itemSchema).max(700), extra: z.array(extraSchema).max(150).default([]),
  operations: z.array(operationSchema).max(40).optional(),
  contentType: z.string().optional(), unreadable: z.array(z.string()).default([]), unresolved: z.array(z.string()).default([]) });
const issueSchema = z.object({ id: z.string(), kind: z.string(), severity: z.enum(["major", "minor"]),
  evidence: z.string().max(2500), instruction: z.string().max(2500) });
const reviewSchema = z.object({ coverage: z.enum(["complete", "uncertain", "missing"]),
  regions: z.array(z.object({zone:z.string(),status:z.enum(["checked","protected","missing","unreadable"]),evidence:z.string().min(1).max(500)})).max(9).default([]),
  issues: z.array(issueSchema).max(150), summary: z.string().max(3000).default("模型未提供文字摘要，结论以完整性字段和具体问题为准") });
type Review = z.infer<typeof reviewSchema>;

function limiter(maximum: number) {
  let active = 0;
  const waiting: (() => void)[] = [];
  return async <T>(task: () => Promise<T>): Promise<T> => {
    if (active >= maximum) await new Promise<void>((resolve) => waiting.push(resolve));
    active++;
    try { return await task(); } finally { active--; waiting.shift()?.(); }
  };
}
// All model tasks share a bounded window; slow/limited endpoints reduce pressure.
export function adaptiveLane(maximum = 4) {
  let active = 0, limit = 2, healthy = 0;
  const queue: (() => void)[] = [];
  const drain = () => { while (active < limit && queue.length) { active++; queue.shift()!(); } };
  return async <T>(task: () => Promise<T>): Promise<T> => {
    await new Promise<void>(resolve => { queue.push(resolve); drain(); });
    const started = performance.now();
    try {
      const value = await task();
      if (performance.now()-started > 120_000) { limit = Math.max(1, limit-1); healthy = 0; }
      else if (++healthy >= 4) { limit = Math.min(maximum, limit+1); healthy = 0; }
      return value;
    } catch (error) {
      if (/429|50[234]|超时|限流/.test((error as Error).message)) { limit = Math.max(1, limit-1); healthy = 0; }
      throw error;
    } finally { active--; drain(); }
  };
}
const modelLane = adaptiveLane();
const translationLane = modelLane;
const reviewLane = modelLane;
const geometryLane = limiter(2);

export function repeatedMajorIssues(rounds: { issues: PageIssue[] }[]) {
  if (rounds.length < 3) return false;
  const signatures = rounds.slice(-3).map(r => new Set(r.issues.filter(i => i.severity === "major").map(i => `${i.id}:${i.kind}`)));
  // Stop only an unchanged set across three checks, never merely a shared issue.
  return signatures[0].size > 0 && signatures.every(s => s.size === signatures[0].size && [...s].every(k => signatures[0].has(k)));
}
export function changedReviewRows(rows: Row[], before: Row[], issues: PageIssue[]) {
  if (issues.some(i => i.id === "missing")) return null;
  const ids = new Set(issues.map(i => i.id));
  const selected = rows.filter(row => ids.has(row.id) || row.mergedIds?.some(id => ids.has(id)) ||
    JSON.stringify(row) !== JSON.stringify(before.find(old => old.id === row.id)));
  if (!selected.length || selected.length > 18 || issues.some(i => !rows.some(r => r.id === i.id || r.mergedIds?.includes(i.id)))) return null;
  return selected;
}
const activeJobs = new Map<string, Promise<void>>();
const translationMemories = new Map<string, Map<string, string>>();
export function translationMemoryKey(rows: Row[], index: number, kind: string) {
  const row = rows[index];
  if (row.origin !== "native" || !row.source.trim() || row.mergedIds?.length) return null;
  // Exact source (including every number), adjacent wording, geometry and page type.
  // Memory belongs to one document job, never crosses customer/file boundaries.
  return JSON.stringify([kind, rows[index-1]?.source || "", row.source, rows[index+1]?.source || "",
    row.box.map(v => +v.toFixed(3)), row.fontSize, row.angle]);
}
const writeQueues = new Map<string, Promise<void>>();
export const jobFolder = (docId: string, jobId: string) => path.join(dataRoot, "pipelines", docId, jobId);

export async function replaceManifest(tmp: string, destination: string, move = rename) {
  // Windows readers/antivirus may hold the previous manifest briefly. Keep
  // the old valid file until atomic replacement succeeds; never truncate it.
  for (let attempt = 0; ; attempt++) {
    try { await move(tmp, destination); return; }
    catch (error) {
      if (attempt >= 5 || !["EPERM", "EACCES", "EBUSY"].includes((error as NodeJS.ErrnoException).code || "")) throw error;
      await delay(40 * (attempt + 1));
    }
  }
}

async function saveJob(job: PipelineJob) {
  const folder = jobFolder(job.docId, job.id);
  const serialized = JSON.stringify(job, null, 2);
  const next = (writeQueues.get(job.id) || Promise.resolve()).catch(() => {}).then(async () => {
    await mkdir(folder, { recursive: true });
    const tmp = path.join(folder, "job.tmp");
    await writeFile(tmp, serialized);
    await replaceManifest(tmp, path.join(folder, "job.json"));
  });
  writeQueues.set(job.id, next);
  await next;
}

export async function latestPipeline(docId: string): Promise<PipelineJob | null> {
  try {
    const id = (await readFile(path.join(dataRoot, "pipelines", docId, "latest"), "utf8")).trim();
    if (!z.string().uuid().safeParse(id).success) return null;
    const job = JSON.parse(await readFile(path.join(jobFolder(docId, id), "job.json"), "utf8")) as PipelineJob;
    if (job.status === "running" && !activeJobs.has(job.id) && process.env.PIPELINE_EXTERNAL_WORKER !== "true") {
      try {
        const pid = Number(await readFile(path.join(jobFolder(docId, id), "owner.pid"), "utf8"));
        if (!Number.isInteger(pid) || pid <= 0) throw Error("No worker");
        process.kill(pid, 0);
      } catch { job.status = "interrupted"; }
    }
    if (process.env.PIPELINE_EXTERNAL_WORKER === "true") {
      const queue=db().prepare("SELECT state FROM pipeline_queue WHERE job_id=?").get(job.id) as {state:string}|undefined;
      job.workerState=queue?.state;
    }
    return job;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

async function images(file: string, detailed: boolean) {
  const original = await readFile(file);
  const make = async (input: Sharp) => `data:image/jpeg;base64,${(await input.resize({ width: 1600, height: 1600,
    fit: "inside", withoutEnlargement: true }).jpeg({ quality: 88 }).toBuffer()).toString("base64")}`;
  const output = [await make(sharp(original))];
  if (detailed) {
    const info = await sharp(original).metadata();
    const w = info.width!, h = info.height!;
    const cw = Math.ceil(w * .54), ch = Math.ceil(h * .54);
    for (const [left, top] of [[0, 0], [w-cw, 0], [0, h-ch], [w-cw, h-ch]])
      output.push(await make(sharp(original).extract({ left, top, width: cw, height: ch })));
  }
  return output;
}

async function changedRegionImages(folder: string, rows: Row[], issues: PageIssue[], before: Row[]) {
  const selected = changedReviewRows(rows, before, issues);
  if (!selected) return null;
  const output: string[] = [];
  for (const row of selected) {
    for (const name of ["original.png", "translated.png"]) {
      const source = await readFile(path.join(folder, name));
      const info = await sharp(source).metadata();
      const footprints = [row.box, row.layoutBox || row.box, ...(row.sourceBoxes || [])];
      const x = Math.min(...footprints.map(b => b[0])), y = Math.min(...footprints.map(b => b[1]));
      const w = Math.max(...footprints.map(b => b[0]+b[2]))-x, h = Math.max(...footprints.map(b => b[1]+b[3]))-y;
      const left = Math.max(0, Math.floor((x-.008)*info.width!));
      const top = Math.max(0, Math.floor((y-.008)*info.height!));
      const right = Math.min(info.width!, Math.ceil((x+w+.008)*info.width!));
      const bottom = Math.min(info.height!, Math.ceil((y+h+.008)*info.height!));
      const crop = await sharp(source).extract({ left, top, width: right-left, height: bottom-top })
        .resize({ width: 1400, height: 900, fit: "inside", withoutEnlargement: true }).jpeg({ quality: 92 }).toBuffer();
      output.push(`data:image/jpeg;base64,${crop.toString("base64")}`);
    }
  }
  return { output, ids: selected.map((row) => row.id) };
}

async function repairEvidence(folder: string, rows: Row[], issues: PageIssue[]) {
  const selected = rows.filter(row => issues.some(issue=>issue.id===row.id)).slice(0,18);
  const request = path.join(folder,"inspect-regions.json");
  await writeFile(request,JSON.stringify(selected.map(row=>({id:row.id,box:row.box}))));
  await geometryLane(()=>pageWorker(["crops",folder,request]));
  const images: string[] = [];
  for (let i=0;i<selected.length;i++) for (const version of ["source","translated"]) {
    const buffer=await readFile(path.join(folder,`inspect-${i}-${version}.png`));
    images.push(await labelImage(`data:image/png;base64,${buffer.toString("base64")}`,`${version==="source"?"ORIGINAL":"CHINESE RESULT"} ID ${selected[i].id}`));
  }
  return {ids:selected.map(row=>row.id),images};
}

async function labelImage(value: string, label: string) {
  const buffer = Buffer.from(value.split(",")[1], "base64");
  const info = await sharp(buffer).metadata();
  const banner = Buffer.from(`<svg width="${info.width}" height="38"><rect width="100%" height="38" fill="#e7eef5"/><text x="12" y="27" font-size="22" font-family="Arial" fill="#183c5b">${label}</text></svg>`);
  const labeled = await sharp(buffer).extend({ top: 38, bottom: 0, left: 0, right: 0, background: "white" })
    .composite([{ input: banner, top: 0, left: 0 }]).jpeg({ quality: 88 }).toBuffer();
  return `data:image/jpeg;base64,${labeled.toString("base64")}`;
}

export function applyTranslation(rows: Row[], data: z.infer<typeof translationSchema>, complete: boolean): Row[] {
  const ids = new Set(data.items.map((item) => item.id));
  if (ids.size !== data.items.length || data.items.some((item) => !rows.some((row) => row.id === item.id)) ||
    (complete && (data.items.length !== rows.length || rows.some((row) => !ids.has(row.id)))))
    throw new AppError("整页模型结果的编号不完整或重复，此页可以单独重试", 502);
  const output = rows.map((row) => {
    const item = data.items.find((value) => value.id === row.id);
    if (!item) return row;
    if (complete && item.t === undefined) throw new AppError("模型遗漏了应翻译的内容", 502);
    if (!complete && item.t === undefined) return { ...row,
      source: row.origin === "native" ? row.source : item.s?.trim() || row.source,
      layoutBox: item.layoutBox || row.layoutBox, angle: item.angle ?? row.angle,
      fontScale: item.fontScale ?? row.fontScale, reason: item.reason || row.reason };
    const target = item.t ?? row.target;
    if (item.disposition === "translate" && !target.trim()) {
      if (complete) throw new AppError("模型遗漏了应翻译的内容", 502);
      data.unresolved.push(`${row.id}: 修订返回空译文，保留上一版本并继续检查`);
      return row;
    }
    if (item.disposition === "translate" && !row.source && !item.s?.trim()) throw new AppError("模型没有给出视觉识别原文", 502);
    return { ...row, source: row.origin === "native" ? row.source : item.s?.trim() || row.source, target: item.disposition === "translate" ? target : "",
      disposition: item.disposition, reason: item.reason || "",
      // Source masks remain immutable. A placement proposal is independently validated by the renderer.
      layoutBox: item.layoutBox || row.layoutBox, angle: item.angle ?? row.angle, fontScale: item.fontScale ?? row.fontScale };
  });
  for (const extra of data.extra) {
    // Bare quantities, symbols and drawing codes already remain visible on the
    // untouched source page. Adding overlays for them creates false omissions.
    if (/^[\d\s.,+−\-/()%°Øø×x=±:;]+$/.test(extra.s.trim()) || /^[A-Z]$/.test(extra.s.trim())) continue;
    if (extra.box[0] + extra.box[2] > 1.005 || extra.box[1] + extra.box[3] > 1.005) {
      if (!complete) { data.unresolved.push(`拒绝越界新增框：${extra.s}`); continue; }
      throw new AppError("模型新增文字框超出页面", 502);
    }
    // Do not accept a second overlay covering an existing text anchor.
    const duplicate = output.some((row) => {
      const a = extra.box, b = row.box;
      const area = Math.max(0, Math.min(a[0]+a[2],b[0]+b[2])-Math.max(a[0],b[0])) *
        Math.max(0,Math.min(a[1]+a[3],b[1]+b[3])-Math.max(a[1],b[1]));
      return area / Math.max(1e-8, Math.min(a[2]*a[3],b[2]*b[3])) > .45;
    });
    if (duplicate) { data.unresolved.push(`新增区域与已有文字框重叠，未写入：${extra.s}。请调整现有框或执行合并操作，不要重复提交相同新增框。`); continue; }
    output.push({ id: `extra-${output.length + 1}`, source: extra.s, target: extra.t,
      box: extra.box, disposition: "translate", origin: "model", angle: extra.angle, fontScale: extra.fontScale });
  }
  return output;
}

export function applyOperations(rows: Row[], operations: z.infer<typeof operationSchema>[] = []) {
  let output = structuredClone(rows);
  const results: { kind: string; ids: string[]; accepted: boolean; reason: string }[] = [];
  const intersects = (a: number[], b: number[]) => Math.max(0, Math.min(a[0]+a[2],b[0]+b[2])-Math.max(a[0],b[0])) * Math.max(0, Math.min(a[1]+a[3],b[1]+b[3])-Math.max(a[1],b[1]));
  for (const op of operations) {
    try {
      const selected = output.filter(r => op.ids.includes(r.id));
      if (selected.length !== op.ids.length || new Set(op.ids).size !== op.ids.length) throw Error("Unknown or duplicate IDs");
      const x = Math.min(...selected.map(r=>r.box[0])), y = Math.min(...selected.map(r=>r.box[1]));
      const bounds = [x,y,Math.max(...selected.map(r=>r.box[0]+r.box[2]))-x,Math.max(...selected.map(r=>r.box[1]+r.box[3]))-y];
      const others = output.filter(r=>!op.ids.includes(r.id));
      if (op.kind === "merge") {
        if (selected.length < 2 || !op.t?.trim()) throw Error("Merge requires at least two anchors and complete target");
        if (bounds[2]*bounds[3] > selected.reduce((n,r)=>n+r.box[2]*r.box[3],0)*4) throw Error("Anchors too far apart");
        if (others.some(r=>intersects(bounds,r.box)>r.box[2]*r.box[3]*.12)) throw Error("Merged area covers another anchor");
        const source = op.ids.map(id=>selected.find(r=>r.id===id)!.source).join(" ");
        if (deterministicIssues([{...selected[0],source,target:op.t,disposition:"translate"}]).length) throw Error("Merged translation changes numbers/units");
        const master = selected.find(r=>r.id===op.ids[0])!;
        const sizes = selected.map(r=>r.fontSize).filter((v):v is number=>!!v).sort((a,b)=>a-b);
        output = output.filter(r=>!op.ids.includes(r.id) || r.id===master.id).map(r=>r.id!==master.id ? r : {
          ...master, source, target:op.t!, disposition:"translate", box:bounds, layoutBox:undefined,
          fontSize:sizes[Math.floor(sizes.length/2)], sourceBoxes:selected.flatMap(r=>r.sourceBoxes || [r.box]),
          sourceQuads:selected.every(r=>r.sourceQuads?.length) ? selected.flatMap(r=>r.sourceQuads!) : undefined, mergedIds:selected.flatMap(r=>r.mergedIds || [r.id]) });
      } else {
        if (selected.length!==1 || !op.boxes?.length) throw Error("Mask requires one anchor and source boxes");
        for (const b of op.boxes) {
          if (b[0]+b[2]>1 || b[1]+b[3]>1 || b[0]<x-.006 || b[1]<y-.006 || b[0]+b[2]>x+bounds[2]+.006 || b[1]+b[3]>y+bounds[3]+.006) throw Error("Mask outside source neighborhood");
          if (others.some(r=>intersects(b,r.box)>r.box[2]*r.box[3]*.12)) throw Error("Mask covers another anchor");
        }
        selected[0].sourceBoxes=op.boxes;
        selected[0].sourceQuads=undefined;
      }
      results.push({kind:op.kind,ids:op.ids,accepted:true,reason:op.evidence});
    } catch (error) { results.push({kind:op.kind,ids:op.ids,accepted:false,reason:(error as Error).message}); }
  }
  return { rows:output, results };
}

export function deterministicIssues(rows: Row[]): PageIssue[] {
  return rows.flatMap((row) => {
    if (row.disposition === "uncertain") return [{ id: row.id, kind: "ocr", severity: "major" as const,
      evidence: row.reason || row.source, instruction: "对照原图确认原文；无法辨认则保留原文并报告，不要猜测" }];
    if (row.disposition !== "translate") return [];
    return checkTranslation(row.source, row.target, [], "auto", "zh")
      .filter((issue) => /数字或公差发生变化|单位或工程符号不一致/.test(issue))
      .map((issue) => ({ id: row.id, kind: "number", severity: "major" as const,
        evidence: `${row.source} → ${row.target}`, instruction: issue }));
  });
}

function saveSegments(job: PipelineJob, page: PageProgress, rows: Row[]) {
  const connection = db();
  connection.exec("BEGIN IMMEDIATE");
  try {
    connection.prepare("DELETE FROM segments WHERE doc_id=? AND page=?").run(job.docId, page.page);
    const insert = connection.prepare("INSERT INTO segments(id,doc_id,source,target,location,page,box,status,source_lang,target_lang,model,issues,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)");
    for (const row of rows) {
      const issues = page.issues.filter((issue) => issue.id === row.id).map((issue) => `${issue.evidence}；${issue.instruction}`);
      insert.run(randomUUID(), job.docId, row.source, row.target,
        `第 ${page.page} 页 · 整页 ${row.id}`, page.page,
        JSON.stringify({ x: row.box[0], y: row.box[1], w: row.box[2], h: row.box[3], coordinateVersion: 2,
          fontSize: row.fontSize, textAngle: row.angle || 0 }),
        row.disposition === "skip" ? "skipped" : row.target ? "translated" : "draft", "auto", "zh",
        providerStatus().find((model) => model.id === job.provider)?.model || job.provider, JSON.stringify(issues), Date.now());
    }
    connection.prepare("INSERT INTO document_pages(doc_id,page,prepared,quality) VALUES(?,?,1,?) ON CONFLICT(doc_id,page) DO UPDATE SET prepared=1,quality=excluded.quality")
      .run(job.docId, page.page, page.stage === "checked" ? "model-checked" : "partial");
    connection.exec("COMMIT");
  } catch (error) { connection.exec("ROLLBACK"); throw error; }
}

async function processPage(job: PipelineJob, page: PageProgress, source: string, stage: "produce" | "review") {
  const folder = path.join(jobFolder(job.docId, job.id), String(page.page));
  await mkdir(folder, { recursive: true });
  const phase = async (stage: PageStage) => { page.stageStarted = Date.now(); page.stage = stage; await saveJob(job); };
  const timed = async <T>(key: string, fn: () => Promise<T>) => {
    const started = performance.now();
    try { return await fn(); } finally { page.timings[key] = (page.timings[key] || 0) + Math.round(performance.now()-started); }
  };
  const callOnce = async (provider: Provider, task: string, prompt: string, data: unknown, inputImages: string[]) => {
    const requestHash = createHash("sha256").update(JSON.stringify([provider, providerStatus().find(m=>m.id===provider)?.model, prompt, data, inputImages])).digest("hex");
    const started = performance.now();
    rateLimit("model:" + job.userId, dailyModelLimit(), 24*60*60000);
    try {
      const result = await modelJSON(provider, prompt, JSON.stringify(data), inputImages, { maxTokens: task.includes("translate") ? 24000 : 16000, timeoutMs: 180_000 });
      job.calls.push({ page: page.page, task, provider, started: Date.now()-Math.round(performance.now()-started), model: result.model, elapsedMs: Math.round(performance.now()-started), ...result.usage });
      db().prepare("INSERT INTO usage(user_id,provider,model,task,input_tokens,output_tokens,created,doc_id,doc_name,cache_read_tokens,cache_write_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?)")
        .run(job.userId, provider, result.model, "page-" + task, result.usage.input, result.usage.output, Date.now(), job.docId, job.fileName,
          result.usage.cacheRead || 0, result.usage.cacheWrite || 0);
      await writeFile(path.join(folder, `${task}-${page.repairs}.json`), JSON.stringify({...result, requestHash}, null, 2));
      return result.data;
    } catch (error) {
      job.calls.push({ page: page.page, task, provider, started: Date.now()-Math.round(performance.now()-started), model: provider, elapsedMs: Math.round(performance.now()-started), input: 0, output: 0, error: (error as Error).message });
      throw error;
    }
  };
  const call = async (provider: Provider, task: string, prompt: string, data: unknown, inputImages: string[]) => {
    // A schema failure can happen after a paid response has been saved. On resume,
    // reuse that exact response rather than charge for the same review again.
    {
      const requestHash = createHash("sha256").update(JSON.stringify([provider, providerStatus().find(m=>m.id===provider)?.model, prompt, data, inputImages])).digest("hex");
      for (const savedTask of [task, task + "-retry"]) {
        try {
          const saved = JSON.parse(await readFile(path.join(folder, `${savedTask}-${page.repairs}.json`), "utf8"));
          if (saved.requestHash === requestHash) return task.startsWith("check") ? reviewSchema.parse(saved.data) : translationSchema.parse(saved.data);
        } catch (error) {
          if (!(error instanceof z.ZodError) && (error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        }
      }
    }
    try { return await callOnce(provider, task, prompt, data, inputImages); }
    catch (error) {
      if (!/连接超时或不可用|模型服务返回 50[234]/.test((error as Error).message)) throw error;
      return await callOnce(provider, task + "-retry", prompt, data, inputImages);
    }
  };
  page.started ??= Date.now(); page.error = undefined;
  type Checkpoint = { rows: Row[]; round: number; rendered: boolean; reviewed: boolean; review?: Review; issues?: PageIssue[]; beforeRepair?: Row[] };
  let checkpoint: Checkpoint | undefined;
  const persist = async (value: Checkpoint) => {
    value.issues = [...page.issues];
    await writeFile(path.join(folder, "checkpoint.tmp"), JSON.stringify(value));
    await replaceManifest(path.join(folder, "checkpoint.tmp"), path.join(folder, "checkpoint.json"));
    checkpoint = value;
  };
  try {
    try { checkpoint = JSON.parse(await readFile(path.join(folder, "checkpoint.json"), "utf8")); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
    if (checkpoint?.issues) page.issues = checkpoint.issues;
    if (!checkpoint) {
    await phase("preparing");
    await timed("prepare", () => geometryLane(() => pageWorker(["prepare", source, String(page.page), folder])));
    }
    const prepared = JSON.parse(await readFile(path.join(folder, "prepared.json"), "utf8")) as Prepared;
    page.kind = prepared.kind;
    // Detailed crops preserve tiny CAD print after provider-side image resizing.
    const detailed = job.mode === "fine" || prepared.kind === "drawing";
    const originals = await images(path.join(folder, "original.png"), detailed);
    const translatorImages = prepared.sheets?.length ? [originals[0], ...await Promise.all(prepared.sheets.map(async (name) =>
      `data:image/jpeg;base64,${(await readFile(path.join(folder, name))).toString("base64")}`))] : originals;
    const memory = translationMemories.get(job.id) || new Map<string, string>();
    translationMemories.set(job.id, memory);
    const cached = new Map<string, string>();
    prepared.rows.forEach((row, index) => {
      const key = translationMemoryKey(prepared.rows, index, prepared.kind);
      const target = key && memory.get(key);
      if (target && !deterministicIssues([{...row, target, disposition:"translate"}]).length) cached.set(row.id, target);
    });
    const pendingRows = prepared.rows.filter(row => !cached.has(row.id));
    const restoreCached = (translated: Row[]) => [...prepared.rows.map(r => cached.has(r.id)
      ? {...r, target:cached.get(r.id)!, disposition:"translate" as const} : translated.find(t => t.id === r.id)!),
      ...translated.filter(t => !prepared.rows.some(r => r.id === t.id))];
    const anchorInput = pendingRows.map((row) => ({ id: row.id, s: row.source, box: row.box.map((v) => +v.toFixed(4)) }));
    let rows: Row[] = checkpoint?.rows || [];
    let sourceWarnings: string[] = [];
    try { sourceWarnings=JSON.parse(await readFile(path.join(folder,"source-warnings.json"),"utf8")); } catch(error) { if ((error as NodeJS.ErrnoException).code!=="ENOENT") throw error; }
    let initialResponse: z.infer<typeof translationSchema> | undefined;
    const initialData = (raw: unknown) => { initialResponse=translationSchema.parse(raw); return initialResponse; };
    if (!checkpoint) {
    await phase("translating");
    try {
      if (!pendingRows.length) rows = restoreCached([]);
      else rows = await timed("translate", () => translationLane(async () => restoreCached(applyTranslation(pendingRows,
        initialData(await call(job.provider, "translate", pageTranslationPrompt(prepared.kind), { page: page.page, anchors: anchorInput,
          imageOrder: prepared.sheets?.length ? "full original page followed by labeled text-strip sheets; blue labels are exact anchor IDs, not source text" : originals.length > 1 ? "full page then TL,TR,BL,BR crops" : "full original page only" }, translatorImages)), true))));
    } catch (error) {
      // Retry a failed page as a whole at most once; never silently omit missing IDs.
      if (job.calls.filter((entry) => entry.page === page.page && entry.task === "translate").length >= 2) throw error;
      rows = await timed("translate", () => translationLane(async () => restoreCached(applyTranslation(pendingRows,
        initialData(await call(job.provider, "translate-retry", pageTranslationPrompt(prepared.kind),
          { page: page.page, anchors: anchorInput, imageOrder: prepared.sheets?.length ? "full page then text strips labeled with exact anchor IDs" : originals.length > 1 ? "full page then quadrant crops" : "full original page only", previousError: (error as Error).message }, translatorImages)), true))));
    }
    if (initialResponse) sourceWarnings=[...initialResponse.unreadable,...initialResponse.unresolved];
    await writeFile(path.join(folder,"source-warnings.json"),JSON.stringify(sourceWarnings));
    await persist({ rows, round: 0, rendered: false, reviewed: false });
    }
    let lastReview: Review | undefined = checkpoint?.review;
    const maxRepairs = job.mode === "fine" ? 3 : 1;
    for (let round = checkpoint!.round; round <= maxRepairs; round++) {
      const rules = deterministicIssues(rows);
      // Never print a candidate with changed numerical values/units.
      const unsafe = new Set(rules.filter((issue) => issue.kind === "number").map((issue) => issue.id));
      if (!checkpoint?.rendered) {
      await phase("rendering");
      await writeFile(path.join(folder, "rows.json"), JSON.stringify(rows, null, 2));
      await writeFile(path.join(folder, "safe-rows.json"), JSON.stringify(rows.map((row) => unsafe.has(row.id) ? { ...row, target: "" } : row)));
      await timed("render", () => geometryLane(() => pageWorker(["render", folder, path.join(folder, "safe-rows.json")])));
      page.previewVersion++; job.revision++;
      job.firstPreviewMs ??= Date.now()-job.started;
      await persist({ rows, beforeRepair: checkpoint?.beforeRepair, round, rendered: true, reviewed: false });
      }
      await phase("checking_coverage");
      const layout = JSON.parse(await readFile(path.join(folder, "layout.json"), "utf8")) as LayoutEntry[];
      const coverage = checkCompleteness(rows, layout, unsafe);
      page.completeness = coverage.completeness;
      await writeFile(path.join(folder, "completeness.json"), JSON.stringify(coverage, null, 2));
      await phase("checking");
      if (stage === "produce") return;
      if (!checkpoint?.reviewed) {
      const translated = await images(path.join(folder, "translated.png"), detailed);
      const regions = round ? await changedRegionImages(folder, rows, page.issues, checkpoint?.beforeRepair || []) : null;
      const reviewRows = regions ? rows.filter((row) => regions.ids.includes(row.id)) : rows;
      const rawReviewImages = regions ? [originals[0], translated[0], ...regions.output] : [...originals, ...translated];
      const reviewImages = await Promise.all(rawReviewImages.map((value, index) => labelImage(value,
        (regions ? index % 2 === 0 : index < originals.length) ? "ORIGINAL" : "CHINESE RESULT")));
      lastReview = await timed("check", () => reviewLane(async () => reviewSchema.parse(await call(job.reviewer, round ? "check-repair" : "check", pageReviewPrompt(job.provider === job.reviewer, job.mode),
        { page: page.page, originalImageCount: regions ? 1+regions.ids.length : originals.length, translatedImageCount: regions ? 1+regions.ids.length : translated.length,
          imageOrder: regions ? `full original, full translated, then original/translated crop PAIRS in anchor ID order: ${regions.ids.join(",")}` : originals.length > 1 ? "all original images then all translated images; each set: full page then TL,TR,BL,BR crops" : "full original page then full Chinese result page",
          previousIssues: round ? page.issues : [], scope: round ? "Recheck every previous issue after repair; retain unresolved issues. Inspect altered regions for regressions." : "Complete page review",
          items: reviewRows.map((row) => ({ id: row.id, source: row.source, target: row.target, box: row.box.map((v) => +v.toFixed(4)), layoutBox: row.layoutBox, disposition: row.disposition })),
          recognitionWarnings: sourceWarnings,
          repairWarnings: round ? JSON.parse(await readFile(path.join(folder, `unresolved-${round}.json`), "utf8").catch(() => "[]")) : [],
          completeness: coverage.completeness, completenessIssues: coverage.issues,
          deterministicIssues: rules, layout: layout.filter((entry) => entry.flags.length) }, reviewImages))));
      const visual = checkVisualCoverage(lastReview.regions);
      page.visualCoverage = visual.status;
      page.issues = [...rules, ...coverage.issues, ...lastReview.issues, ...visual.issues];
      for (const entry of layout.filter((entry) => !entry.rendered))
        page.issues.push({ id: entry.id, kind: "layout", severity: "major", evidence: entry.flags.join(", "), instruction: "译文尚未写入文件；请缩短等义译文或报告排版限制" });
      if (lastReview.coverage !== "complete" && !page.issues.some((issue) => issue.kind === "omission"))
        page.issues.push({ id: "missing", kind: "omission", severity: "major", evidence: lastReview.summary, instruction: "对照原页检查遗漏；无法确认时明确保留疑点" });
      await copyFile(path.join(folder, "translated.pdf"), path.join(folder, `round-${round+1}.pdf`));
      await writeFile(path.join(folder, `round-${round+1}.json`), JSON.stringify({ rows, review: lastReview, completeness: coverage.completeness, issues: page.issues, finished: Date.now() }, null, 2));
      page.rounds = [...(page.rounds || []).filter((r) => r.round !== round+1), { round: round+1, finished: Date.now(), elapsedMs: Date.now()-page.started!, issues: [...page.issues] }];
      await persist({ rows, round, rendered: true, reviewed: true, review: lastReview });
      await saveJob(job);
      }
      if (!page.issues.some((issue) => issue.severity === "major") || round === maxRepairs) break;
      if (repeatedMajorIssues(page.rounds || [])) {
        page.issues.push({ id: "repair-cycle", kind: "repair", severity: "minor", evidence: "连续三次检查仍报告同一组主要问题", instruction: "已停止重复调用，保留当前译文与未解决问题" });
        await persist({ rows, round, rendered: true, reviewed: true, review: lastReview });
        break;
      }
      page.repairs = round+1;
      await phase("repairing");
      const previousRows = JSON.stringify(rows);
      const evidence = await repairEvidence(folder, rows, page.issues);
      const previousActions = round ? JSON.parse(await readFile(path.join(folder,`actions-${round}.json`),"utf8").catch(()=>"[]")) : [];
      const previousUnresolved = round ? JSON.parse(await readFile(path.join(folder,`unresolved-${round}.json`),"utf8").catch(()=>"[]")) : [];
      const repaired = await timed("repair", () => translationLane(async () => translationSchema.parse(await call(job.provider, "repair", pageRepairPrompt(),
        { page: page.page, items: rows, issues: page.issues, previousActions, previousUnresolved, rendererResults:layout,
          imageOrder: `full ORIGINAL page, full CHINESE RESULT, then high resolution ORIGINAL/CHINESE RESULT pairs for IDs ${evidence.ids.join(",")}` },
        [originals[0], (await images(path.join(folder,"translated.png"),false))[0], ...evidence.images]))));
      rows = applyTranslation(rows, repaired, false);
      const actions = applyOperations(rows,repaired.operations);
      rows = actions.rows;
      await writeFile(path.join(folder,`actions-${round+1}.json`),JSON.stringify(actions.results,null,2));
      await writeFile(path.join(folder, `unresolved-${round+1}.json`), JSON.stringify(repaired.unresolved));
      if (JSON.stringify(rows) === previousRows) {
        page.issues.push({ id: "no-progress", kind: "repair", severity: "minor",
          evidence: "本轮修订没有产生可写入的变化", instruction: "已停止重复修订；请查看仍未解决的检查意见" });
        await persist({ rows, round, rendered: true, reviewed: true, review: lastReview });
        break;
      }
      await persist({ rows, beforeRepair: JSON.parse(previousRows), round: round+1, rendered: false, reviewed: false });
    }
    page.translated = rows.filter((row) => row.disposition === "translate" && row.target).length;
    page.retained = rows.filter((row) => row.disposition === "uncertain").length;
    page.stage = page.issues.some((issue) => issue.severity === "major") ? "needs_attention" : "checked";
    page.finished = Date.now();
    if (page.stage === "checked") {
      prepared.rows.forEach((original, index) => {
        const row = rows.find(r => r.id === original.id);
        const key = translationMemoryKey(prepared.rows, index, prepared.kind);
        if (key && row?.source === original.source && row.disposition === "translate" && !row.mergedIds?.length && row.target &&
          !page.issues.some(issue => issue.id === row.id)) memory.set(key, row.target);
      });
    }
    saveSegments(job, page, rows);
    await writeFile(path.join(folder, "final-review.json"), JSON.stringify(lastReview, null, 2));
  } catch (error) {
    page.stage = "failed"; page.error = (error as Error).message; page.finished = Date.now();
  }
  await saveJob(job);
}

async function runJob(job: PipelineJob) {
  const resumedAt = Date.now(), priorActive = job.activeElapsedMs || 0;
  const heartbeat = setInterval(() => { job.heartbeat = Date.now(); job.activeElapsedMs=priorActive+Date.now()-resumedAt; void saveJob(job).catch(e=>console.error("pipeline heartbeat", e.message)); }, 10000);
  const folder = jobFolder(job.docId, job.id);
  try {
    const source = path.join(dataRoot, "files", job.docId);
    const pending = job.pages.filter((page) => !["checked", "needs_attention"].includes(page.stage));
    let next = 0;
    // Producers finish after the first rendered candidate; reviewers have their own
    // workers and load durable checkpoints, so slow reviews cannot occupy producers.
    const ready: PageProgress[] = [];
    let producersDone = false;
    let wake: (() => void)[] = [];
    const notify = () => { for (const fn of wake.splice(0)) fn(); };
    const reviewers = Promise.all(Array.from({ length: 2 }, async () => {
      while (true) {
        const page = ready.shift();
        if (page) { await processPage(job, page, source, "review"); continue; }
        if (producersDone) return;
        await new Promise<void>((resolve) => wake.push(resolve));
      }
    }));
    try { await Promise.all(Array.from({ length: Math.min(3, pending.length) }, async () => {
      while (next < pending.length) {
        const page = pending[next++];
        await processPage(job, page, source, "produce");
        if (page.stage !== "failed") { ready.push(page); notify(); }
      }
    })); } finally { producersDone = true; notify(); await reviewers; }
    const paths = job.pages.map((page) => path.join(folder, String(page.page), page.previewVersion ? "translated.pdf" : "source.pdf"));
    for (let i=0;i<paths.length;i++) {
      try { await access(paths[i]); } catch {
        await pageWorker(["extract", source, String(job.pages[i].page), paths[i]]);
      }
    }
    await writeFile(path.join(folder, "merge.json"), JSON.stringify(paths));
    await pageWorker(["merge", path.join(folder, "merge.json"), path.join(folder, "result.pdf")]);
    job.outputReady = true;
    for (let round = 1; round <= (job.mode === "fine" ? 4 : 2); round++) {
      const snapshots = job.pages.map((page) => {
        const available = (page.rounds || []).filter((r) => r.round <= round).at(-1);
        return path.join(folder, String(page.page), available ? `round-${available.round}.pdf` : page.previewVersion ? "translated.pdf" : "source.pdf");
      });
      await writeFile(path.join(folder, `merge-round-${round}.json`), JSON.stringify(snapshots));
      await pageWorker(["merge", path.join(folder, `merge-round-${round}.json`), path.join(folder, `round-${round}.pdf`)]);
    }
    job.status = job.pages.every((page) => page.stage === "checked") ? "completed" : "partial";
  } catch (error) { job.error = (error as Error).message; job.status = "partial"; }
  finally { clearInterval(heartbeat); job.activeElapsedMs = priorActive+Date.now()-resumedAt; job.finished = Date.now(); job.elapsedMs = job.finished-job.started; await saveJob(job); }
}

export function validatePipelineModels(provider: Provider, reviewer: Provider, models = providerStatus()) {
  for (const id of new Set([provider, reviewer])) {
    const model = models.find((item) => item.id === id);
    if (!model?.configured || !model.vision) throw new AppError("整页翻译与检查都需要已配置的多模态模型");
  }
}

async function startPipelineUnlocked(input: { docId: string; userId: string; fileName: string; pages: number[];
  provider: Provider; reviewer: Provider; mode: "quick" | "fine"; fresh?: boolean }) {
  validatePipelineModels(input.provider, input.reviewer);
  const queued = db().prepare("SELECT job_id FROM pipeline_queue WHERE doc_id=? AND state IN ('queued','running')").get(input.docId) as {job_id:string}|undefined;
  if (queued) {
    const prior = JSON.parse(await readFile(path.join(jobFolder(input.docId, queued.job_id), "job.json"),"utf8")) as PipelineJob;
    if (prior.provider !== input.provider || prior.mode !== input.mode || JSON.stringify(prior.pages.map(p=>p.page)) !== JSON.stringify(input.pages)) throw new AppError("当前文件已有后台任务，请完成后再更改模型、模式或页码",409);
    return prior;
  }
  const existing = await latestPipeline(input.docId);
  if (existing?.status === "running") {
    if (existing.provider !== input.provider) throw new AppError("翻译任务正在运行，不能切换模型", 409);
    return existing;
  }
  const bytes = await readFile(path.join(dataRoot, "files", input.docId));
  const profile = identifyFile(bytes, input.fileName);
  if (profile.format !== "pdf") throw new AppError("逐页视觉检查当前用于 PDF；其他格式使用其原格式处理流程");
  const inspected = await pageWorker(["inspect", path.join(dataRoot, "files", input.docId)]);
  if (inspected.encrypted) throw new AppError("请先解除 PDF 密码保护");
  const totalPages = Number(inspected.pages);
  if (!input.pages.length || new Set(input.pages).size !== input.pages.length || input.pages.some((p) => !Number.isInteger(p) || p < 1 || p > totalPages))
    throw new AppError("所选页码无效");
  const resume = existing && !input.fresh && existing.status !== "completed" && existing.provider === input.provider && existing.reviewer === input.reviewer &&
    existing.mode === input.mode && JSON.stringify(existing.pages.map((p) => p.page)) === JSON.stringify(input.pages);
  const job: PipelineJob = resume ? existing : { id: randomUUID(), docId: input.docId, userId: input.userId, fileName: input.fileName,
    format: profile.format, totalPages, provider: input.provider, reviewer: input.reviewer, mode: input.mode,
    pinnedModel: providerStatus().find(m=>m.id===input.provider)?.model, status: "running", started: Date.now(), revision: 0, calls: [], outputReady: false,
    pages: input.pages.map((page) => ({ page, stage: "queued", timings: {}, issues: [], previewVersion: 0, repairs: 0 })) };
  if (!resume && !input.fresh && existing?.mode === "quick" && input.mode === "fine" && existing.provider === input.provider && existing.reviewer === input.reviewer) {
    for (const page of job.pages) {
      const old = existing.pages.find(p => p.page === page.page);
      if (!old?.previewVersion) continue;
      const from = path.join(jobFolder(input.docId, existing.id), String(page.page));
      const to = path.join(jobFolder(input.docId, job.id), String(page.page));
      const checkpoint = JSON.parse(await readFile(path.join(from, "checkpoint.json"), "utf8"));
      await cp(from, to, { recursive: true, filter: src => {
        const name = path.basename(src);
        return !/^(check|repair|translate-|actions-|unresolved-|round-|checkpoint|final-review)/.test(name);
      } });
      await writeFile(path.join(to, "checkpoint.json"), JSON.stringify({ rows: checkpoint.rows, round: 0, rendered: false, reviewed: false }));
      page.previewVersion = 1;
      job.reusedFrom = existing.id;
    }
  }
  job.userId = input.userId;
  job.status = "running"; job.finished = undefined; job.error = undefined;
  await saveJob(job);
  if (process.env.PIPELINE_EXTERNAL_WORKER === "true") {
    try {
      db().prepare("INSERT INTO pipeline_queue(job_id,doc_id,user_id,state,updated) VALUES(?,?,?,'queued',?) ON CONFLICT(job_id) DO UPDATE SET state='queued',attempts=0,error=NULL,updated=excluded.updated").run(job.id,job.docId,job.userId,Date.now());
    } catch { throw new AppError("当前文件已有任务正在提交，请刷新查看",409); }
    await writeFile(path.join(dataRoot,"pipelines",job.docId,"latest"),job.id);
    return job;
  }
  await writeFile(path.join(jobFolder(job.docId, job.id), "owner.pid"), String(process.pid));
  await writeFile(path.join(dataRoot, "pipelines", job.docId, "latest"), job.id);
  const work = runJob(job).finally(() => { activeJobs.delete(job.id); writeQueues.delete(job.id); translationMemories.delete(job.id); });
  activeJobs.set(job.id, work);
  return job;
}

export async function waitPipeline(jobId: string) { await activeJobs.get(jobId); }

// Called only by the single durable worker (OS flock held for its lifetime).
export async function recoverQueuedJobs() {
  db().prepare("UPDATE pipeline_queue SET state='queued',updated=? WHERE state='running'").run(Date.now());
  const docs=db().prepare("SELECT id FROM documents").all() as {id:string}[];
  for (const doc of docs) {
    try {
    const job=await latestPipeline(doc.id);
    if (job?.status==="running" && !db().prepare("SELECT job_id FROM pipeline_queue WHERE job_id=?").get(job.id))
      db().prepare("INSERT OR IGNORE INTO pipeline_queue(job_id,doc_id,user_id,state,updated) VALUES(?,?,?,'queued',?)").run(job.id,job.docId,job.userId,Date.now());
    } catch { console.error(`Cannot recover legacy manifest for document ${doc.id}; other tasks will continue`); }
  }
}
export async function runNextQueuedJob() {
  const entry=db().prepare("SELECT * FROM pipeline_queue WHERE state='queued' ORDER BY updated LIMIT 1").get() as {job_id:string;doc_id:string;user_id:string;attempts:number}|undefined;
  if (!entry) return false;
  db().prepare("UPDATE pipeline_queue SET state='running',attempts=attempts+1,updated=? WHERE job_id=? AND state='queued'").run(Date.now(),entry.job_id);
  try {
    const job=JSON.parse(await readFile(path.join(jobFolder(entry.doc_id,entry.job_id),"job.json"),"utf8")) as PipelineJob;
    if (entry.attempts>=3) throw Error("任务连续中断三次，已暂停自动恢复；请检查错误后手动继续");
    const account=db().prepare("SELECT active FROM users WHERE id=?").get(entry.user_id) as {active:number}|undefined;
    if (!account?.active) throw Error("账号已停用，任务暂停");
    if (!db().prepare("SELECT id FROM documents WHERE id=? AND user_id=?").get(entry.doc_id,entry.user_id)) throw Error("原文件记录不存在");
    const configured=providerStatus().find(m=>m.id===job.provider);
    if (job.pinnedModel && configured?.model !== job.pinnedModel) throw Error("模型配置已变更，任务暂停，避免续跑时自动换模型");
    job.recoveries=entry.attempts; job.status="running"; job.error=undefined;
    await runJob(job);
    db().prepare("UPDATE pipeline_queue SET state='done',updated=? WHERE job_id=?").run(Date.now(),entry.job_id);
  } catch(error) {
    const message=(error as Error).message;
    db().prepare("UPDATE pipeline_queue SET state='failed',error=?,updated=? WHERE job_id=?").run(message,Date.now(),entry.job_id);
    try { const file=path.join(jobFolder(entry.doc_id,entry.job_id),"job.json"); const job=JSON.parse(await readFile(file,"utf8"));job.status="interrupted";job.error=message; await saveJob(job); } catch {}
  } finally { writeQueues.delete(entry.job_id); translationMemories.delete(entry.job_id); }
  return true;
}

export async function startPipeline(input: Parameters<typeof startPipelineUnlocked>[0]) {
  const owner=randomUUID(), now=Date.now();
  const lock=db().prepare("INSERT INTO pipeline_submission_locks(doc_id,owner,expires) VALUES(?,?,?) ON CONFLICT(doc_id) DO UPDATE SET owner=excluded.owner,expires=excluded.expires WHERE pipeline_submission_locks.expires<?").run(input.docId,owner,now+600000,now);
  if (!lock.changes) throw new AppError("任务正在提交，请稍后刷新查看",409);
  try { return await startPipelineUnlocked(input); }
  finally { db().prepare("DELETE FROM pipeline_submission_locks WHERE doc_id=? AND owner=?").run(input.docId,owner); }
}
