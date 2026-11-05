"use client";
import { useEffect, useRef } from "react";
import { Loader2, CircleAlert, Download } from "lucide-react";
import { withBasePath } from "@/lib/base-path";
import { pageStageLabels, type PipelineJob } from "@/lib/page-pipeline-types";

export default function PipelinePreview({ job, page, onPageChange }: {
  job: PipelineJob; page: number; onPageChange: (page: number) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const reported = useRef<number | null>(null);
  useEffect(() => {
    if (reported.current === page) { reported.current = null; return; }
    const element = root.current?.querySelector<HTMLElement>(`[data-pipeline-page="${page}"]`);
    if (element && root.current) root.current.scrollTop = element.offsetTop;
  }, [page]);
  return <div className="pipeline-scroll" ref={root} onScroll={() => {
    if (!root.current) return;
    const anchor = root.current.scrollTop + 80;
    let current = job.pages[0]?.page;
    for (const el of root.current.querySelectorAll<HTMLElement>("[data-pipeline-page]")) {
      if (el.offsetTop > anchor) break;
      current = Number(el.dataset.pipelinePage);
    }
    if (current && current !== page) { reported.current = current; onPageChange(current); }
  }}>
    {job.pages.map((item) => <section className="pipeline-page" key={item.page} data-pipeline-page={item.page}>
      <div className="pipeline-page-status"><b>第 {item.page} 页</b><span data-stage={item.stage}>{pageStageLabels[item.stage]}</span>
        {!item.finished && item.stageStarted && job.status === "running" && <small>本步骤 {Math.max(0,Math.floor((Date.now()-item.stageStarted)/1000))} 秒</small>}
        {item.visualCoverage && <small>整页漏译核对：{item.visualCoverage === "complete" ? "已核对" : "仍有疑点"}</small>}
        {item.finished && <small>{((item.finished-(item.started || item.finished))/1000).toFixed(1)} 秒</small>}
        {!!item.previewVersion && <small>{item.stage === "checked" ? "已检查译文" : item.stage === "needs_attention" ? "译文仍有疑点" : "初稿 · 检查未完成"}</small>}
        {!!item.previewVersion && <a href={withBasePath(`/api/documents/${job.docId}/pipeline/pages/${item.page}/file?job=${job.id}`)} download={`${job.fileName.replace(/\.pdf$/i, "")}-第${item.page}页-中文译文.pdf`} aria-label={`下载第 ${item.page} 页中文 PDF`}><Download size={15}/> 下载本页</a>}</div>
      {item.previewVersion ? <img loading="lazy" src={withBasePath(`/api/documents/${job.docId}/pipeline/pages/${item.page}?job=${job.id}&v=${item.previewVersion}`)} alt={`第 ${item.page} 页中文译文，${pageStageLabels[item.stage]}`} /> :
        <div className="pipeline-page-wait"><Loader2 size={20} className={item.stage === "failed" ? "" : "animate-spin"}/>{item.error || pageStageLabels[item.stage]}</div>}
      {item.error && !!item.previewVersion && <p className="full-issues"><CircleAlert size={14}/>{item.error}。当前显示已生成的译文，检查尚未完成。</p>}
      {!!item.issues.length && <details><summary><CircleAlert size={14}/> {item.issues.length} 项检查意见</summary>
        <ul>{item.issues.map((issue, index) => <li key={index}><b>{issue.severity === "major" ? "待解决" : "提醒"}：</b>{issue.evidence}<br/>{issue.instruction}</li>)}</ul></details>}
    </section>)}
  </div>;
}
