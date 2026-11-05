import type { PageIssue } from "./page-pipeline-types";

type Row = { id: string; source: string; target: string; disposition: string; reason?: string };
export type LayoutEntry = { id: string; rendered: boolean; flags: string[] };
export type Completeness = {
  expected: number; written: number; preserved: number; uncertain: number;
  missingIds: string[]; status: "complete" | "incomplete" | "uncertain";
};

// Engineering labels are translatable; abbreviations and identifiers alone are not.
const meaningful = /\b(?:notes?|sections?|views?|details?|scales?|concrete|reinforcement|diameter|parapet|barrier|shall|must|minimum|maximum|typical|level|length|width|height|pile|cast|drawing|sheet|title|date|revision|approved|checked|drawn|prepared|tolerance|cover|stirrup|spacer|duct|drain|schedule|load|steel|slab|column|girder|abutment|bearing|plan|elevation|bridge|joint|beam|prestress|tendon|bolts?|nuts?|embed|construction|rujuk|nota|keratan|pelan|aras)\b/i;
export function likelyProtected(row: Row) {
  const source = row.source.trim();
  if (/^[\d\s.,+−\-/()%°Øø×x=±:;]+$/.test(source)) return true;
  if (/^(?:[A-Z]{1,8}[-/\d.]+)+[A-Z\d./-]*$/i.test(source)) return true;
  if (!meaningful.test(source) && /logo|signature|stamp|company|address|标志|签名|印章|公司|地址/i.test(row.reason || "")) return true;
  if (/\p{Script=Han}/u.test(source) && !/[A-Za-z]{2,}/.test(source)) return true;
  return !meaningful.test(source) && /^(?:[A-Z]{1,2}|nr|sum|no\.?|nos\.?|mm|cm|km|kg|kN|MPa|m[²³23]?)$/.test(source);
}

export function checkCompleteness(rows: Row[], layout: LayoutEntry[], unsafeIds: Set<string> = new Set()) {
  const rendered = new Map(layout.map((entry) => [entry.id, entry]));
  const result: Completeness = { expected: 0, written: 0, preserved: 0, uncertain: 0, missingIds: [], status: "complete" };
  const issues: PageIssue[] = [];
  for (const row of rows) {
    if (row.disposition === "uncertain") { result.uncertain++;
      issues.push({id:row.id,kind:"ocr",severity:"major",evidence:row.reason || "原文无法确认",instruction:"对照原图确认；无法辨认必须保留疑点，不能标为完整"}); continue; }
    if (row.disposition !== "translate" && likelyProtected(row)) { result.preserved++; continue; }
    if (row.target === row.source && /\p{Script=Han}/u.test(row.source) && !/[A-Za-z]{2,}/.test(row.source)) { result.preserved++; continue; }
    result.expected++;
    const entry = rendered.get(row.id);
    const reason = unsafeIds.has(row.id) ? "数字或单位核对未通过，原文暂时保留" :
      !row.target.trim() ? "应译文字没有译文" :
      ((!/\p{Script=Han}/u.test(row.target) || row.target === row.source) && /[A-Za-z]{2,}/.test(row.source) && !likelyProtected(row)) ? "应译文字仍未译成中文" :
      !entry?.rendered ? `译文未写入 PDF：${entry?.flags.join(", ") || "缺少写入记录"}` : "";
    if (reason) {
      result.missingIds.push(row.id);
      issues.push({ id: row.id, kind: "omission", severity: "major", evidence: `${reason}；原文：${row.source}`,
        instruction: unsafeIds.has(row.id) ? "对照原图确认数字，修正识别原文和译文后重新排版" : "优先补齐该区域；保留全部义务、否定、数字和单位，不能通过标记 skip 隐藏遗漏" });
    } else result.written++;
  }
  result.status = result.missingIds.length ? "incomplete" : result.uncertain ? "uncertain" : "complete";
  return { completeness: result, issues };
}

export const coverageZones=["top-left","top-center","top-right","middle-left","middle-center","middle-right","bottom-left","bottom-center","bottom-right"];
export function checkVisualCoverage(regions: {zone:string;status:string;evidence:string}[]) {
  const issues:PageIssue[]=[];
  if (regions.length!==9 || new Set(regions.map(r=>r.zone)).size!==9 || coverageZones.some(zone=>!regions.some(r=>r.zone===zone)) || regions.some(r=>!r.evidence.trim())) {
    issues.push({id:"missing",kind:"omission",severity:"major",evidence:"整页九区域核对证据不完整",instruction:"逐区对照原图与译图，补齐标题、边角、小字与图签的完整性核对"});
  }
  for (const region of regions) if (["missing","unreadable"].includes(region.status)) issues.push({id:"missing",kind:"omission",severity:"major",evidence:region.zone+"："+region.evidence,instruction:"补译遗漏；无法辨认时保留原文并明确标记，不要猜测"});
  return {status: (regions.some(r=>r.status==="missing") ? "missing" : issues.length ? "uncertain" : "complete") as "complete"|"uncertain"|"missing",issues};
}
