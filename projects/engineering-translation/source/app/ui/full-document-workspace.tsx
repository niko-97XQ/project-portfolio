"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, CircleAlert, Download, FileText, Languages, Loader2, MessageSquareText, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "./api";
import { withBasePath } from "@/lib/base-path";
import PdfCanvasPreview from "./pdf-canvas-preview";
import PipelinePreview from "./pipeline-preview";
import type { PipelineJob } from "@/lib/page-pipeline-types";
import type { FileProfile } from "@/lib/file-profile";
import type { AdviceMessage, Doc, Provider, Segment, TypographyCheck, TypographyReport } from "@/lib/types";

type ModelStatus = { id: Provider; configured: boolean; model: string; vision: boolean };
type Preview = { url: string; text: string; fileName: string; retainedCount: number; layoutRetained: string[]; typography: TypographyReport | null };
const typographyLabels: Record<TypographyCheck["flags"][number], string> = {
  reduced: "为适应原位置缩小字号", small: "字号小于 7 磅", style: "原文字形未完全保留（粗体/斜体）",
  missing: "中文字体缺少字符，保留原文", unknown: "未取得原文字号", "no-fit": "空间不足，保留原文", orientation: "此处文字方向复杂，保留原文", boundary: "文字区域跨越图线或表格边界，保留原文",
};
const visualKinds = ["pdf", "png", "jpg", "jpeg"];

function selectedPages(spec: string, count: number) {
  if (!spec.trim()) return Array.from({ length: count }, (_, i) => i + 1);
  const pages = new Set<number>();
  for (const token of spec.split(",")) {
    const m = token.trim().match(/^(\d+)(?:\s*-\s*(\d+))?$/);
    if (!m) throw new Error("页码格式应为 1-3,5；留空表示全部");
    const start = Number(m[1]), end = Number(m[2] || m[1]);
    if (start < 1 || end < start || end > count) throw new Error(`页码须在 1-${count} 之间`);
    for (let i = start; i <= end; i++) pages.add(i);
  }
  return [...pages].sort((a, b) => a - b);
}

function pageRangeLabel(pages: number[]) {
  const ranges: string[] = [];
  let first = pages[0], last = first;
  for (const page of pages.slice(1)) {
    if (page === last + 1) { last = page; continue; }
    ranges.push(first === last ? String(first) : `${first}-${last}`);
    first = last = page;
  }
  ranges.push(first === last ? String(first) : `${first}-${last}`);
  return ranges.join(",");
}

function groups(rows: Segment[], maxItems = 20, maxChars = 12000, includeTarget = false) {
  const result: Segment[][] = [];
  let chars = 0;
  for (const row of rows) {
    let group = result.at(-1);
    const length = row.source.length + (includeTarget ? row.target.length : 0);
    if (!group || group.length >= maxItems || chars + length > maxChars) {
      group = []; result.push(group); chars = 0;
    }
    group.push(row);
    chars += length;
  }
  return result;
}

async function runLimited<T>(items: T[], limit: number, task: (item: T) => Promise<void>,
  onComplete: (completed: number, total: number) => void) {
  let next = 0;
  let completed = 0;
  let failed = false;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (!failed && next < items.length) {
      const item = items[next++];
      try {
        await task(item);
        onComplete(++completed, items.length);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  });
  const results = await Promise.allSettled(workers);
  const rejection = results.find((result): result is PromiseRejectedResult => result.status === "rejected");
  if (rejection) throw rejection.reason;
}

function needsAttention(segment: Segment) {
  if (segment.status === "skipped") return false;
  return !segment.target.trim() || (segment.review != null && segment.review.verdict !== "pass") || segment.issues.some((issue) =>
    /数字或公差发生变化|单位或工程符号不一致|文字识别置信度较低|图片文字无法确认|尚无译文|模型无法判断/.test(issue));
}

async function exportedFile(doc: Doc, pages: string, partial = false) {
  const response = await fetch(withBasePath(`/api/documents/${doc.id}/export?format=original&pages=${encodeURIComponent(pages)}`), { cache: "no-store" });
  if (!response.ok) {
    const error = await response.json() as { error?: string };
    throw new Error(error.error || "生成译文文件失败");
  }
  const blob = await response.blob();
  let layoutRetained: string[] = [];
  let typography: TypographyReport | null = null;
  try { layoutRetained = JSON.parse(decodeURIComponent(response.headers.get("X-Translation-Layout-Locations") || "%5B%5D")); } catch { /* Older server response. */ }
  try { typography = JSON.parse(decodeURIComponent(response.headers.get("X-Translation-Typography") || "null")); } catch { /* Older server response. */ }
  return { url: URL.createObjectURL(blob), text: doc.kind === "txt" ? await blob.text() : "",
    fileName: doc.name.replace(/\.[^.]+$/, "") + (partial ? "-部分译文." : "-译文.") + doc.kind,
    retainedCount: Number(response.headers.get("X-Translation-Retained-Count") || 0), layoutRetained, typography };
}

function SegmentEditor({ doc, segment, reviewModel, models, onUpdate, readOnly = false }: {
  doc: Doc; segment: Segment; reviewModel: Provider; models: ModelStatus[]; onUpdate: (rows: Segment[]) => void; readOnly?: boolean;
}) {
  const [source, setSource] = useState(segment.source);
  const [target, setTarget] = useState(segment.target);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<AdviceMessage[]>([]);
  useEffect(() => {
    let live = true;
    void api<AdviceMessage[]>(`documents/${doc.id}/advice/${segment.id}`)
      .then((rows) => { if (live) setMessages(rows); }).catch(() => {});
    return () => { live = false; };
  }, [doc.id, segment.id]);
  async function save(skipped = false) {
    setBusy(true);
    try {
      const rows = await api<Segment[]>(`documents/${doc.id}/segments/${segment.id}`, { method: "PATCH",
        body: JSON.stringify({ source, target: skipped ? "" : target, reviewed: false, skipped, updated: segment.updated }) });
      onUpdate(rows);
      toast.success(skipped ? "已保留原文" : "译文已保存，右侧文件将更新");
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(false); }
  }
  async function ask() {
    if (!question.trim()) return;
    setBusy(true);
    try {
      const rows = await api<AdviceMessage[]>(`documents/${doc.id}/advice/${segment.id}`, { method: "POST",
        body: JSON.stringify({ provider: reviewModel, message: question.trim() }) });
      setMessages(rows); setQuestion("");
    } catch (error) { toast.error((error as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="full-segment-editor">
    <div className="full-segment-title"><b>{segment.location}</b><span>{segment.status === "skipped" ? "保留原文" : segment.target ? "已有译文" : "未翻译"}</span></div>
    <label>原文<Textarea value={source} readOnly={readOnly || doc.kind === "xlsx" || busy} onChange={(e) => setSource(e.target.value)} /></label>
    <label>译文<Textarea value={target} readOnly={readOnly} disabled={busy} onChange={(e) => setTarget(e.target.value)} /></label>
    {!!segment.issues.length && <div className="full-issues"><CircleAlert size={15}/>{segment.issues.join("；")}</div>}
    {segment.review && <div className="full-issues"><CircleAlert size={15}/><div>
      <b>模型检查：{segment.review.verdict === "pass" ? "未发现问题" : segment.review.verdict === "revise" ? "建议修改" : "无法确认"}</b>
      {!!segment.review.findings.length && <p>{segment.review.findings.join("；")}</p>}
      {segment.review.verdict === "revise" && segment.review.suggestion !== segment.target &&
        <p>建议译文：{segment.review.suggestion}</p>}
    </div></div>}
    {!readOnly && <div className="full-segment-actions">
      <Button size="sm" variant="outline" disabled={busy || (source === segment.source && target === segment.target)} onClick={() => void save()}>保存修改</Button>
      <Button size="sm" variant="ghost" disabled={busy || segment.status === "skipped"} onClick={() => void save(true)}>保留原文</Button>
    </div>}
    {readOnly && <p className="muted-note">本次译文由逐页流程生成，以下对话用于咨询，不会修改已生成的文件。</p>}
    <div className="full-advice">
      <b><MessageSquareText size={15}/> 模型意见</b>
      <div className="full-advice-messages">{messages.length ? messages.map((m) => <p key={m.id}><strong>{m.role === "user" ? "你" : m.model || "模型"}：</strong>{m.content}</p>) : <p>可询问术语、遗漏或工程参数；意见不会自动改写文件。</p>}</div>
      <Textarea value={question} maxLength={1000} disabled={busy} onChange={(e) => setQuestion(e.target.value)} placeholder="例如：这里的标高和强度是否翻译准确？" />
      <Button size="sm" variant="outline" disabled={busy || !question.trim() || !models.find((m) => m.id === reviewModel)?.configured} onClick={() => void ask()}>发送问题</Button>
    </div>
  </div>;
}

export default function FullDocumentWorkspace({ doc, docs, segments, setSegments, model, setModel,
  reviewModel, translationMode, setTranslationMode,
  models, uploadFile, openDocument, switchDocument, reloadDocs, run, busy, uploadInput }: {
  doc: Doc | null; docs: Doc[]; segments: Segment[]; setSegments: (rows: Segment[]) => void;
  model: Provider; setModel: (value: Provider) => void;
  reviewModel: Provider;
  translationMode: "quick" | "fine"; setTranslationMode: (value: "quick" | "fine") => void;
  models: ModelStatus[]; uploadFile: (file?: File) => Promise<void>; openDocument: (id: string) => Promise<void>;
  switchDocument: (id: string) => Promise<Doc>;
  reloadDocs: () => Promise<void>; run: (label: string, task: () => Promise<void>) => Promise<void>;
  busy: string; uploadInput: React.RefObject<HTMLInputElement | null>;
}) {
  const [meta, setMeta] = useState<{ pageCount: number; unit: string; legacySegments: boolean; preparedPages: number[]; incompletePages: number[]; profile?: FileProfile } | null>(null);
  const [pipeline, setPipeline] = useState<PipelineJob | null>(null);
  const pipelineRef = useRef<PipelineJob | null>(null);
  const [pageSpec, setPageSpec] = useState("");
  const [pdfPage, setPdfPage] = useState(1);
  const [progress, setProgress] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [onlyAttention, setOnlyAttention] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [originalText, setOriginalText] = useState("");
  const previewUrl = useRef("");
  const docId = doc?.id;
  const modelLabel = model === "deepseek" ? "DeepSeek" : model === "sol" ? "Sol 6.1" : "Astra";
  useEffect(() => {
    setPipeline(null);
    pipelineRef.current = null;
    if (!docId || doc?.kind !== "pdf") return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    let loadedPages = -1;
    const poll = async () => {
      try {
        const value = await api<PipelineJob | null>(`documents/${docId}/pipeline`);
        if (live) {
          setPipeline(value);
          pipelineRef.current = value;
          if (value && loadedPages === -1) {
            setPageSpec(pageRangeLabel(value.pages.map((p) => p.page)));
            setTranslationMode(value.mode);
            if (value.provider === "compatible" || value.provider === "deepseek" || value.provider === "sol") setModel(value.provider);
          }
          const finishedPages = value?.pages.filter((p) => p.finished).length ?? 0;
          if (value && finishedPages !== loadedPages) {
            const result = await api<{ segments: Segment[] }>(`documents/${docId}`);
            if (live) { setSegments(result.segments); loadedPages = finishedPages; }
          }
          timer = setTimeout(poll, value?.status === "running" ? 2000 : 6000);
        }
      } catch { if (live) timer = setTimeout(poll, 6000); }
    };
    void poll();
    return () => { live = false; clearTimeout(timer); };
  }, [docId, doc?.kind]);
  useEffect(() => {
    if (!docId) return;
    let live = true;
    setMeta(null);
    setPageSpec("");
    setProgress("");
    setPreview(null);
    void api<{ pageCount: number; unit: string; legacySegments: boolean; preparedPages: number[]; incompletePages: number[] }>(`documents/${docId}/meta`)
      .then((data) => {
        if (!live) return;
        if (pipelineRef.current?.docId === docId)
          setPageSpec(pageRangeLabel(pipelineRef.current.pages.map((p) => p.page)));
        else if (!data.legacySegments && data.preparedPages.length && data.preparedPages.length < data.pageCount)
          setPageSpec(pageRangeLabel(data.preparedPages));
        setMeta(data);
      }).catch((error) => toast.error((error as Error).message));
    return () => { live = false; };
  }, [docId]);
  useEffect(() => {
    if (!docId || doc?.kind !== "txt") return;
    let live = true;
    void fetch(withBasePath(`/api/documents/${docId}/file`)).then((r) => r.text()).then((value) => { if (live) setOriginalText(value); });
    return () => { live = false; };
  }, [docId, doc?.kind]);
  useEffect(() => () => { if (previewUrl.current) URL.revokeObjectURL(previewUrl.current); }, []);

  const visibleSegments = meta?.legacySegments ? [] : segments;
  const attentionSegments = visibleSegments.filter(needsAttention);
  const listedSegments = onlyAttention && attentionSegments.length ? attentionSegments : visibleSegments;
  const selected = listedSegments.find((s) => s.id === selectedId) || listedSegments[0];
  const ready = models.find((m) => m.id === model)?.configured;
  const visual = !!doc && visualKinds.includes(doc.kind);
  let chosenPages: number[] | null = null;
  if (meta && pageSpec.trim()) {
    try { chosenPages = selectedPages(pageSpec, meta.pageCount); } catch { /* Editing an incomplete range must not crash the preview. */ }
  }
  const resultRows = visibleSegments.filter((s) => !chosenPages || chosenPages.includes(s.page));
  const uncoveredDrawingPages = doc?.kind === "pdf" && chosenPages ? chosenPages.filter((page) => {
    const rows = segments.filter((s) => s.page === page);
    return meta?.incompletePages.includes(page) || (rows.length > 0 && rows.every((s) => !s.box || s.box.y >= 0.85));
  }) : [];

  async function refreshPreview(activeDoc = doc, pages = pageSpec, failLoudly = false) {
    if (!activeDoc) return;
    if (pipeline?.docId === activeDoc.id) return;
    setPreviewBusy(true);
    setPreviewError("");
    try {
      const latestMeta = await api<{ pageCount: number; unit: string; legacySegments: boolean; preparedPages: number[]; incompletePages: number[] }>(`documents/${activeDoc.id}/meta`);
      setMeta(latestMeta);
      const selected = selectedPages(pages, latestMeta.pageCount);
      const partial = activeDoc.kind === "pdf" && latestMeta.incompletePages.some((page) => selected.includes(page));
      const result = await exportedFile(activeDoc, pages, partial);
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      previewUrl.current = result.url;
      setPreview(result);
      return result;
    } catch (error) {
      setPreviewError((error as Error).message);
      setPreview(null);
      if (failLoudly) throw error;
      return null;
    } finally { setPreviewBusy(false); }
  }
  useEffect(() => {
    if (!doc || !meta || meta.legacySegments || !segments.some((s) => s.target.trim())) return;
    const frame = window.requestAnimationFrame(() => { void refreshPreview(doc, pageSpec); });
    // Existing files get their result preview when opened; an active translation refreshes explicitly.
    return () => window.cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, meta?.pageCount, pageSpec]);

  async function translateAll() {
    if (!doc || !meta) return;
    if (doc.kind === "pdf") {
      await run("正在启动逐页任务", async () => {
        selectedPages(pageSpec, meta.pageCount);
        const job = await api<PipelineJob>(`documents/${doc.id}/pipeline`, { method: "POST",
          body: JSON.stringify({ pages: pageSpec, provider: model, reviewer: reviewModel, mode: translationMode }) });
        setPipeline(job);
        setProgress("逐页任务已启动；每页生成后立即核对完整性并进行模型检查，有问题自动反馈修订。");
      });
      return;
    }
    await run("整份翻译中", async () => {
      setProgress("");
      const pages = selectedPages(pageSpec, meta.pageCount);
      if (!ready) throw new Error("请先配置翻译模型 API");
      if (translationMode === "fine" &&
        (!models.find((m) => m.id === reviewModel)?.configured))
        throw new Error("精细翻译需要已配置的检查模型");
      let activeDoc = doc;
      let current = segments;
      if (visual && meta.legacySegments) {
        setProgress("正在为旧版选区记录创建整份翻译副本");
        const copy = await api<{ id: string }>(`documents/${doc.id}/whole-copy`, { method: "POST" });
        activeDoc = await switchDocument(copy.id);
        current = [];
        await reloadDocs();
      }
      try {
        if (visual) {
          setProgress(`正在识别文件 · 0/${pages.length} ${meta.unit}`);
          await runLimited(pages, 2, async (page) => {
            await api<Segment[]>(`documents/${activeDoc.id}/prepare-page`, { method: "POST",
              body: JSON.stringify({ page, provider: model, from: "auto" }) });
          }, (done, total) => setProgress(`正在识别文件 · ${done}/${total} ${meta.unit}`));
          current = (await api<{ doc: Doc; segments: Segment[] }>(`documents/${activeDoc.id}`)).segments;
          setSegments(current);
        }
        const chosen = new Set(pages);
        const pending = current.filter((s) => chosen.has(s.page) && s.status !== "skipped" &&
          !s.target.trim() && s.source.trim() && !s.issues.some((issue) =>
            /图片文字无法确认|模型无法判断该区域是否需要翻译|模型建议：该区域可能无需翻译/.test(issue)));
        const translationBatches = groups(pending);
        if (translationBatches.length) {
          setProgress(`正在翻译 · 0/${translationBatches.length} 批`);
          await runLimited(translationBatches, 2, async (group) => {
            await api<Segment[]>(`documents/${activeDoc.id}/translate`, { method: "POST",
              body: JSON.stringify({ ids: group.map((s) => s.id), provider: model, from: "auto", to: "zh", mode: translationMode }) });
          }, (done, total) => setProgress(`正在翻译 · ${done}/${total} 批`));
          current = (await api<{ doc: Doc; segments: Segment[] }>(`documents/${activeDoc.id}`)).segments;
          setSegments(current);
        }
        if (translationMode === "fine") {
          const reviewBatches = groups(current.filter((s) => chosen.has(s.page) && s.target.trim() &&
            s.status !== "skipped" && !s.review), 10, 10000, true);
          if (reviewBatches.length) {
            setProgress(`正在校验 · 0/${reviewBatches.length} 批`);
            await runLimited(reviewBatches, 2, async (group) => {
              await api<Segment[]>(`documents/${activeDoc.id}/review-batch`, { method: "POST",
                body: JSON.stringify({ ids: group.map((s) => s.id), provider: reviewModel }) });
            }, (done, total) => setProgress(`正在校验 · ${done}/${total} 批`));
            current = (await api<{ doc: Doc; segments: Segment[] }>(`documents/${activeDoc.id}`)).segments;
            setSegments(current);
          }
        }
      } catch (error) {
        try {
          current = (await api<{ doc: Doc; segments: Segment[] }>(`documents/${activeDoc.id}`)).segments;
          setSegments(current);
        } catch { /* Keep the original processing error. */ }
        setProgress(`翻译已暂停：${(error as Error).message}。已完成内容暂存在服务器，可在保留期限内继续。`);
        throw error;
      }
      setProgress("正在生成同格式译文");
      try {
        await reloadDocs();
        const finished = await refreshPreview(activeDoc, pageSpec, true);
        const uncovered = activeDoc.kind === "pdf" ? pages.filter((page) => {
          const rows = current.filter((s) => s.page === page);
          return meta.incompletePages.includes(page) || (rows.length > 0 && rows.every((s) => !s.box || s.box.y >= 0.85));
        }) : [];
        if (uncovered.length) {
          setProgress(`尚未完成：第 ${uncovered.join("、")} 页图面文字仍有识别或排版缺口。右侧是部分译文预览，请勿作为完成版使用。`);
          toast.warning("图面正文尚未完整翻译，已显示部分译文预览");
          return;
        }
        if (finished) downloadFile(finished);
      } catch (error) {
        setProgress(`译文生成已暂停：${(error as Error).message}。已完成内容暂存在服务器，可在保留期限内继续。`);
        throw error;
      }
      setProgress(`已处理 ${pages.length} ${meta.unit}；已尝试自动下载，请确认浏览器下载完成，右侧仍可再次下载`);
      toast.success("译文文件已生成并开始下载");
    });
  }

  function downloadFile(file: Preview) {
    const anchor = document.createElement("a");
    anchor.href = file.url;
    anchor.download = file.fileName;
    anchor.click();
  }
  function downloadResult() {
    if (preview) downloadFile(preview);
  }
  function drop(event: React.DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    if (!busy) void uploadFile(event.dataTransfer.files?.[0]);
  }
  function sheetPreview(source: boolean) {
    return <div className="full-sheet-preview"><table><thead><tr><th>位置</th><th>{source ? "原文" : "译文"}</th></tr></thead><tbody>
      {(source ? visibleSegments : resultRows).slice(0, 150).map((row) => <tr key={row.id}><td>{row.sheet ? `${row.sheet} · ${row.cell}` : row.location}</td><td>{source ? row.source : row.target || row.source}</td></tr>)}
    </tbody></table>{visibleSegments.length > 150 && <p>预览前 150 项；下载包含全部内容。</p>}</div>;
  }
  return <div className="full-document-page">
    <div className="page-heading full-page-heading"><div><span className="eyebrow">ENGINEERING DOCUMENT TRANSLATION</span><h1>工程文件翻译</h1><p>左侧原文件，右侧同格式中文译文。自动识别混合语言，默认翻译全部内容。</p></div></div>
    <input ref={uploadInput} type="file" aria-label="上传文件" hidden onChange={(event) => { void uploadFile(event.target.files?.[0]); event.target.value = ""; }} />
    <section className={`surface full-job-card ${doc ? "" : "full-job-card-empty"}`}>
      <div className="full-job-heading"><div><b>{doc?.name || "翻译设置"}</b><span>{doc ? `${meta ? `${meta.pageCount} ${meta.unit}` : "正在读取文件"} · 自动识别原文语言 → 简体中文` : `${modelLabel} 翻译，上传文件后直接开始`}</span></div><span>{doc ? `${doc.kind.toUpperCase()} 原格式输出` : "自动识别混合语言 → 中文"}</span></div>
      <div className={`full-controls ${doc ? "" : "full-controls-preupload"}`}>
        <label>翻译模型<select aria-label="翻译模型" value={model} disabled={!!busy || pipeline?.status === "running"} onChange={e => setModel(e.target.value as Provider)}>
          <option value="compatible">Astra</option><option value="sol">Sol 6.1</option><option value="deepseek">DeepSeek</option>
        </select></label>
        {doc && <label>{meta?.unit === "工作表" ? "工作表序号" : "翻译页码"}<Input value={pageSpec} disabled={!!busy} onChange={(e) => setPageSpec(e.target.value)} placeholder="留空翻译全部；例 1-3,5" /></label>}
        <div className="full-mode-row" role="group" aria-label="翻译模式">
          <button type="button" className={translationMode === "quick" ? "active" : ""} aria-pressed={translationMode === "quick"} disabled={!!busy || pipeline?.status === "running"} onClick={() => setTranslationMode("quick")}><b>快速翻译</b><span>完整性与数字检查 · 最多修订 1 轮</span></button>
          <button type="button" className={translationMode === "fine" ? "active" : ""} aria-pressed={translationMode === "fine"} disabled={!!busy || pipeline?.status === "running"} onClick={() => setTranslationMode("fine")}><b>精细翻译</b><span>增加版式细查 · 最多修订 3 轮</span></button>
        </div>
      </div>
      <p className="full-progress">{doc?.kind === "pdf" ? `逐页核对翻译完整性和中文写入；${translationMode === "fine" ? "精细模式继续检查排版与图线。" : "快速模式重点检查明显错译与遮挡。"}` : translationMode === "fine" ? `翻译后进行规则检查与 ${modelLabel} 自查。` : "翻译后进行数字、单位与遗漏规则检查。"}</p>
      {meta?.profile && <p className="full-progress">文件识别：{meta.profile.format.toUpperCase()} · {meta.pageCount} {meta.unit} · {meta.profile.route}</p>}
      {pipeline && pipeline.docId === docId && <p className="full-progress" aria-live="polite">逐页任务：{pipeline.pages.filter((p) => ["checked", "needs_attention"].includes(p.stage)).length}/{pipeline.pages.length} 页已检查
        {pipeline.status === "running" ? (pipeline.workerState === "queued" ? " · 已排队，后台将自动处理" : " · 后台处理中") : pipeline.status === "interrupted" ? " · 任务中断，可继续" : ` · 总用时 ${((pipeline.elapsedMs || 0)/1000).toFixed(1)} 秒`}
        {pipeline.pages.some((p) => p.completeness) && ` · 已写入中文 ${pipeline.pages.reduce((n,p) => n+(p.completeness?.written || 0),0)}/${pipeline.pages.reduce((n,p) => n+(p.completeness?.expected || 0),0)} 个应译区域（已识别部分）`}
        {!!pipeline.recoveries && ` · 已自动恢复 ${pipeline.recoveries} 次`}
        {pipeline.status === "running" && pipeline.workerState === "running" && pipeline.heartbeat && Date.now()-pipeline.heartbeat > 45000 && " · 后台心跳暂未更新，正在等待恢复"}
        {pipeline.pages.some((p) => p.stage === "failed") && " · 部分页面失败，可单独续跑"}
        {pipeline.pages.some((p) => p.stage === "needs_attention") && " · 部分页面仍有疑点，请查看右侧检查意见"}</p>}
      {doc && <div className="full-job-actions"><Button disabled={!!busy || !meta || !ready || pipeline?.status === "running"} onClick={() => void translateAll()}>{busy || pipeline?.status === "running" ? <Loader2 className="animate-spin" size={16}/> : <Languages size={16}/>} {busy || (pipeline?.status === "running" ? "逐页处理中" : pipeline?.mode === "quick" && translationMode === "fine" ? "复用译文并精细检查" : pipeline?.status === "interrupted" || pipeline?.pages.some((p) => p.stage === "failed") ? "继续未完成页面" : "开始翻译")}</Button>
        <span>自动保留图标、签名及无法可靠识别的内容；数字或单位不一致时保留该处原文并提示。</span></div>}
      {progress && <p className="full-progress" aria-live="polite">{progress}</p>}
      {!!uncoveredDrawingPages.length && <p className="muted-note" role="status">第 {uncoveredDrawingPages.join("、")} 页图面文字仍有识别或排版缺口；右侧仅供查看部分结果。</p>}
      {meta?.legacySegments && <p className="muted-note">旧版选区记录会保留；开始整份翻译时自动创建原文件副本。</p>}
    </section>
    <div className="full-document-body">
      <section className={`surface full-preview full-source-panel ${dragging ? "dragging" : ""}`}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
        onDrop={drop}>
        <div className="full-pane-heading"><div><span className="full-pane-index">01 / SOURCE</span><h2>原文件</h2></div><div className="full-upload-actions"><Button size="sm" variant="outline" disabled={!!busy} onClick={() => uploadInput.current?.click()}><Upload size={15}/> {doc ? "更换文件" : "上传文件"}</Button></div></div>
        {!doc ? <div className="full-drop-empty"><div className="full-drop-icon"><Upload size={28}/></div><h3>拖入工程文件</h3><p>支持 PDF、Excel、TXT 和图片；也可点击“上传文件”选择</p><small>单文件不超过 20MB · DWG/DXF 可先导出 PDF</small></div> : <>
          <div className="full-file-meta"><FileText size={15}/><span>{doc.name}</span><em>{doc.kind.toUpperCase()}</em></div>
          <div className="full-document-canvas">{doc.kind === "pdf" ? <PdfCanvasPreview key={doc.id} src={withBasePath(`/api/documents/${doc.id}/file`)} label="原文件" page={pdfPage} onPageChange={setPdfPage} /> :
            ["png", "jpg", "jpeg"].includes(doc.kind) ? <img src={withBasePath(`/api/documents/${doc.id}/file`)} alt="原文件" /> :
            doc.kind === "txt" ? <pre>{originalText}</pre> : sheetPreview(true)}</div>
        </>}
        {dragging && <div className="full-drop-overlay">松开即可上传文件</div>}
      </section>
      <div className="full-translation-bridge"><ArrowRight size={18}/></div>
      <section className="surface full-preview full-result-panel">
        <div className="full-pane-heading"><div><span className="full-pane-index">02 / TRANSLATION</span><h2>{pipeline ? "逐页中文译文" : uncoveredDrawingPages.length ? "中文部分译文 · 未完成" : "中文译文"}</h2></div><Button size="sm" disabled={(pipeline ? !pipeline.outputReady : !preview) || !!busy} onClick={() => {
          if (pipeline) { const link = document.createElement("a"); link.href = withBasePath(`/api/documents/${pipeline.docId}/pipeline/file?job=${pipeline.id}`); link.download = doc?.name.replace(/\.pdf$/i, "-中文译文.pdf") || "中文译文.pdf"; link.click(); }
          else downloadResult();
        }}><Download size={15}/> {(pipeline && pipeline.status !== "completed") || uncoveredDrawingPages.length ? "下载部分译文" : "下载文件"}</Button></div>
        <div className="full-file-meta"><Languages size={15}/><span>{preview?.fileName || (doc ? `${doc.name.replace(/\.[^.]+$/, "")}-译文.${doc.kind}` : "等待上传原文件")}</span><em>中文</em></div>
        <div className="full-document-canvas">{pipeline && pipeline.docId === docId ? <PipelinePreview job={pipeline} page={pdfPage} onPageChange={setPdfPage}/> : previewBusy ? <div className="full-result-empty"><Loader2 className="animate-spin" size={30}/><h3>正在生成译文预览</h3></div> :
          preview ? doc?.kind === "pdf" ? <PdfCanvasPreview key={preview.url} src={preview.url} label="中文译文" page={pdfPage} onPageChange={setPdfPage} /> :
            doc && ["png", "jpg", "jpeg"].includes(doc.kind) ? <img src={preview.url} alt="翻译后的图片" /> :
            doc?.kind === "txt" ? <pre>{preview.text}</pre> : sheetPreview(false) :
            <div className="full-result-empty"><div className="full-result-symbol"><Languages size={29}/></div><h3>{previewError || "译文将在这里显示"}</h3><p>{doc ? "点击“开始翻译”，完成后即可预览并下载。" : "上传原文件后，点击开始翻译。"}</p></div>}</div>
        {!!preview?.retainedCount && <p className="full-retained-note"><CircleAlert size={14}/>{preview.retainedCount} 处保留原文，包括无需翻译的编号与符号、识别或参数疑点，以及版式空间不足的位置；详情见下方质量提示。</p>}
        {!!preview?.layoutRetained.length && <details className="full-layout-locations"><summary>查看版式受限的位置（{preview.layoutRetained.length} 处）</summary><p>{preview.layoutRetained.join("、")}</p></details>}
        {preview?.typography && <details className="full-layout-locations">
          <summary>字体与字号核对 · {preview.typography.checked} 处 · {preview.typography.warnings} 处提醒</summary>
          <p>中文字体：{preview.typography.font}。优先沿用原文字号；字体会替换为支持中文的字体。以下字号单位为磅（pt）。</p>
          <ul>{preview.typography.examples.map((item, index) => <li key={index}>
            {item.location}：{item.sourceSize ?? "未知"} → {item.outputSize ?? "保留原文"}
            {item.flags.length ? `；${item.flags.map((flag) => typographyLabels[flag]).join("；")}` : "；字号一致"}
            <br/>字体：{item.sourceFont || "未知"} → {item.font}
          </li>)}</ul>
          {preview.typography.warnings > preview.typography.examples.length && <p>这里展示前 {preview.typography.examples.length} 处提醒，请结合页面预览核对。</p>}
          <p>此检查核对字体、字号、缺字和文字框容量；图文遮挡及阅读效果仍需对照页面预览。</p>
        </details>}
        {preview && doc?.kind === "xlsx" && <p className="muted-note">此处按单元格展示内容；下载的 XLSX 保留原工作簿样式与公式。</p>}
      </section>
    </div>
    {!doc && !!docs.length && <section className="full-recent-files"><h2>最近文件</h2><div className="full-file-list">{docs.slice(0, 8).map((item) => <Button key={item.id} variant="outline" onClick={() => void openDocument(item.id)}>{item.name}</Button>)}</div></section>}
    {doc && !!visibleSegments.length && <details className="surface full-check-details"><summary><span><CircleAlert size={17}/> 质量提示与模型意见</span><strong>{attentionSegments.length} 项提示 · {visibleSegments.filter((s) => s.target.trim()).length}/{visibleSegments.length} 项已有译文</strong></summary>
      <div className="full-check-content"><p>数字、单位、OCR 疑点供核对参考；文件生成不需要人工确认。无法可靠识别的文字会保留原文。</p>
        <div className="full-list-toolbar"><button type="button" className={!onlyAttention ? "active" : ""} onClick={() => setOnlyAttention(false)}>全部 {visibleSegments.length}</button><button type="button" className={onlyAttention ? "active" : ""} onClick={() => setOnlyAttention(true)}>有提示 {attentionSegments.length}</button></div>
        <div className="full-check-grid"><div className="full-segment-list">{listedSegments.map((s) => <button type="button" key={s.id} className={s.id === selected?.id ? "active" : ""} onClick={() => setSelectedId(s.id)}><span>{s.location}</span><small>{needsAttention(s) ? "需核对" : s.target ? "已翻译" : s.status === "skipped" ? "保留原文" : "未翻译"}</small></button>)}</div>
          {selected && <SegmentEditor key={`${selected.id}-${selected.updated}`} doc={doc} segment={selected} reviewModel={reviewModel} models={models} readOnly={!!pipeline} onUpdate={(rows) => { setSegments(rows); void refreshPreview(doc, pageSpec); }}/>}</div>
      </div>
    </details>}
  </div>;
}
