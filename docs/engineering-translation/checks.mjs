// Flag differences, never silently convert engineering values or units.
export function checkTranslation(source, target, terms = [], from = "en", to = "zh") {
    const issues = [];
    if (!target.trim())
        return ["尚无译文"];
    const months = ["january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december"];
    const countDigits = { 一: "1", 二: "2", 两: "2", 三: "3", 四: "4", 五: "5", 六: "6", 七: "7", 八: "8", 九: "9" };
    const numbers = (s, counterpart) => ((s.replace(/(?<![零〇一二两三四五六七八九十百千万])([一二两三四五六七八九])(?=层|个|根|组|条|处)/g, (word) => {
        // Normalize a single Chinese count only when the other side contains
        // that exact Arabic value. This avoids turning ordinary "one" wording
        // into a new number, and does not treat 十二 as 二.
        const value = countDigits[word];
        return (counterpart.match(/\d+(?:[.,]\d+)*/g) || []).some(token => token === value) ? value : word;
    }).replace(/(?:第)?一(?=期|阶段)/g, "1")
        .replace(/\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(?=\d{4}\b)/gi, (month) => String(months.findIndex((name) => name.startsWith(month.replace(/\W/g, "").toLowerCase())) + 1) + " ")
        .replace(/\bF0R\b/gi, "FOR").replace(/\b0F\b/gi, "OF"))
        .match(/[-+−]?\d+(?:[.,]\d+)*(?:\s*[±]\s*\d+(?:\.\d+)?)?/g) || [])
        .map((x) => x.replace(/\s/g, ""))
        .sort();
    if (JSON.stringify(numbers(source, target)) !== JSON.stringify(numbers(target, source)))
        issues.push("数字或公差发生变化，请核对原文");
    const units = (s) => (s.replace(/(\d)\s*MM\b/g, "$1 mm").match(/(?:(?<![A-Za-z])(?:mm|cm|km|m|MPa|kPa|Pa|kN|N|kg|t|kN\/m|N\/mm)(?:[²³23])?(?![A-Za-z0-9])|[%‰°Ø⌀Φφ])/g) || []).sort();
    if (JSON.stringify(units(source)) !== JSON.stringify(units(target)))
        issues.push("单位或工程符号不一致，请核对");
    for (const term of terms) {
        const candidates = from === "auto" ? [term.en, term.ms] : [term[from]];
        for (const candidate of candidates) {
            if (candidate && source.toLowerCase().includes(candidate.toLowerCase()) &&
                !target.toLowerCase().includes(term[to].toLowerCase()))
                issues.push(`术语建议：${candidate} → ${term[to]}`);
        }
    }
    if (from !== to &&
        source.trim() === target.trim() &&
        /[a-zA-Z]{3}/.test(source))
        issues.push("译文与原文相同，请确认是否需要翻译");
    return [...new Set(issues)];
}
