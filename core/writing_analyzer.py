"""
WritingAnalyzer — Ported from fiction-forge (geobond13/fiction-forge)
Contains 26 prose patterns + 4 Chinese extras.
Source: https://github.com/geobond13/fiction-forge (MIT)
"""

import re
from collections import Counter

FICTION_PATTERNS = [
  {
    "name": "em_dash",
    "regex": "—",
    "target_density": 1.3,
    "weight": 3,
    "description": "Em-dashes — overuse creates choppy, self-conscious prose",
    "suggestion": "Replace some em-dashes with commas or periods"
  },
  {
    "name": "like_a_an",
    "regex": "\\blike\\s+(?:a|an)\\b",
    "target_density": 0.8,
    "weight": 2,
    "description": "Simile constructions — keep the strongest, cut the rest",
    "suggestion": "Replace with direct description or stronger verb"
  },
  {
    "name": "as_if",
    "regex": "\\bas\\s+(?:if|though)\\b",
    "target_density": 0.4,
    "weight": 1.5,
    "description": "As-if/as-though hedging — weakens immediacy",
    "suggestion": "Delete or replace with direct statement"
  },
  {
    "name": "the_way",
    "regex": "\\bthe\\s+way\\s+\\w+\\s+\\w+",
    "target_density": 0.5,
    "weight": 1.5,
    "description": "The-way constructions — often signals telling over showing",
    "suggestion": "Replace with concrete sensory detail"
  },
  {
    "name": "hedging_verbs",
    "regex": "\\b(?:seemed?\\s+to|appeared?\\s+to|as\\s+if\\s+(?:he|she|it|they)\\s+might)\\b",
    "target_density": 0.2,
    "weight": 1,
    "description": "Hedging verbs — undercuts narrative authority",
    "suggestion": "Replace with direct, definite verbs"
  },
  {
    "name": "sentence_start_and",
    "regex": "(?:^|\\n)\\s*And\\s+(?!so\\b)",
    "target_density": 0.7,
    "weight": 0.5,
    "description": "Sentences starting with And",
    "suggestion": "Merge sentences or use different connector"
  },
  {
    "name": "sentence_start_but",
    "regex": "(?:^|\\n)\\s*But\\s+",
    "target_density": 0.5,
    "weight": 0.5,
    "description": "Sentences starting with But",
    "suggestion": "Merge sentences or reorder"
  },
  {
    "name": "filter_words",
    "regex": "\\bI\\s+(?:noticed|realized|felt|saw|heard|watched)\\b",
    "target_density": 0.7,
    "weight": 1.5,
    "description": "Filter words — narrator between reader and action",
    "suggestion": "Describe the scene directly, remove the filter layer"
  },
  {
    "name": "found_myself",
    "regex": "\\b(?:found\\s+(?:myself|himself|herself|themselves|itself))\\b",
    "target_density": 0.2,
    "weight": 2,
    "description": "Found-myself construction — classic AI overuse",
    "suggestion": "Rewrite as direct action description"
  },
  {
    "name": "something_like",
    "regex": "\\b(?:something\\s+(?:like|between|close\\s+to))\\b",
    "target_density": 0.2,
    "weight": 2,
    "description": "Something-like hedging",
    "suggestion": "Delete or replace with precise description"
  },
  {
    "name": "for_a_long_moment",
    "regex": "\\bfor\\s+a\\s+(?:long|brief)\\s+moment\\b",
    "target_density": 0.15,
    "weight": 2,
    "description": "For-a-moment padding",
    "suggestion": "Delete this filler phrase"
  },
  {
    "name": "or_perhaps",
    "regex": "\\b[Oo]r\\s+perhaps\\b",
    "target_density": 0.15,
    "weight": 1.5,
    "description": "Or-perhaps equivocation",
    "suggestion": "Delete or change to definite statement"
  },
  {
    "name": "passive_emotion",
    "regex": "\\bwas\\s+(?:filled|overcome|consumed|seized)\\s+(?:with|by)\\b",
    "target_density": 0.2,
    "weight": 2,
    "description": "Passive emotion — telling instead of showing",
    "suggestion": "Show emotion through action and dialogue"
  },
  {
    "name": "named_emotion_bare",
    "regex": "\\bfelt\\s+(?:anger|grief|fear|sadness|joy|rage|sorrow|love|despair|shame|guilt|relief|pity)\\b",
    "target_density": 0.4,
    "weight": 1.5,
    "description": "Named emotion — show, don't label",
    "suggestion": "Imply emotion through behavior and dialogue"
  },
  {
    "name": "emotional_softening",
    "regex": "\\bbut\\s+there\\s+was\\s+no\\s+malice\\b|\\bbut\\s+(?:he|she|they|I)\\s+meant\\s+no\\b|\\bthough\\s+(?:he|she|they|I)\\s+didn'?t\\s+mean\\b",
    "target_density": 0.15,
    "weight": 2.5,
    "description": "Emotional softening — defusing reader tension",
    "suggestion": "Keep tension, do not excuse the character"
  },
  {
    "name": "show_then_tell",
    "regex": "(?:In that (?:gesture|moment|silence|word|look|single)|That was (?:the|what)|That's what|That'?s the thing|Meaning:|What I mean is|The point (?:was|is)|In other words)",
    "target_density": 0.15,
    "weight": 2.5,
    "description": "Show-then-tell",
    "suggestion": "Delete the explanation, let the action speak"
  },
  {
    "name": "the_kind_of",
    "regex": "\\bthe kind of \\w+ that\\b",
    "target_density": 0.2,
    "weight": 1.5,
    "description": "The-kind-of-X-that construction",
    "suggestion": "Use a concrete adjective directly"
  },
  {
    "name": "the_particular",
    "regex": "\\bthe particular \\w+",
    "target_density": 0.15,
    "weight": 1.5,
    "description": "The-particular — false precision",
    "suggestion": "Be specific or omit"
  },
  {
    "name": "not_x_y_fragment",
    "regex": "\\. Not [A-Za-z]+\\.\\s+(?:Not [A-Za-z]+\\.\\s+)*[A-Z]",
    "target_density": 0.3,
    "weight": 1,
    "description": "Not-X fragment chains",
    "suggestion": "Merge fragments into flowing prose"
  },
  {
    "name": "retrospective_foreshadow",
    "regex": "\\byears later\\b|\\bmonths later I would\\b|\\bonly later would I\\b|\\bI didn't know then\\b|\\b[Ii]n hindsight\\b|\\blooking back\\b|\\bgotten ahead of myself\\b",
    "target_density": 0.15,
    "weight": 2,
    "description": "Retrospective foreshadowing",
    "suggestion": "Limit retrospective narration, stay in the present"
  },
  {
    "name": "something_vague",
    "regex": "\\b[Ss]omething (?:shifted|changed|stirred|broke|moved|passed between)\\b",
    "target_density": 0.08,
    "weight": 2,
    "description": "Vague something",
    "suggestion": "Use a specific noun"
  },
  {
    "name": "not_x_not_y_but_z",
    "regex": "\\.\\s+Not [A-Za-z]+\\.\\s+Not [A-Za-z]+\\.\\s+But ",
    "target_density": 0.25,
    "weight": 1.5,
    "description": "Not-X, Not-Y, But-Z rhetorical pattern",
    "suggestion": "Simplify the construction"
  },
  {
    "name": "something_complicated",
    "regex": "\\b[Ss]omething complicated\\b",
    "target_density": 0,
    "weight": 3,
    "description": "Something complicated — never the right word",
    "suggestion": "Describe what actually makes it complicated"
  },
  {
    "name": "silence_was_not",
    "regex": "\\b[Ss]ilence (?:was|were|seemed?) not (?:ordinary|normal|usual|natural|the kind)\\b",
    "target_density": 0.05,
    "weight": 2,
    "description": "Silence-was-not characterization",
    "suggestion": "Show character reaction instead"
  },
  {
    "name": "triple_simile_stack",
    "regex": "(?:\\blike\\b[^.!?\\n]{0,80}\\blike\\b[^.!?\\n]{0,80}\\blike\\b)",
    "target_density": 0,
    "weight": 3,
    "description": "Triple simile stack",
    "suggestion": "Delete two similes, keep the strongest one"
  },
  {
    "name": "might_have_been",
    "regex": "\\bmight have been\\b",
    "target_density": 0.15,
    "weight": 1,
    "description": "Might-have-been conditional past",
    "suggestion": "Use definite past tense"
  }
]

CHINESE_EXTRAS = [
  {
    "name": "adverb_clusters_cn",
    "regex": "(?:轻轻地|缓缓地|静静地|默默地|渐渐地|悄悄地|慢慢地|迅速地|突然地)",
    "target_density": 0.4,
    "weight": 1,
    "severity": "low",
    "description": "Chinese adverb clusters",
    "suggestion": "Use stronger verbs"
  },
  {
    "name": "emotion_tagging_cn",
    "regex": "(?:感到一阵|心中涌起|心底升起|一股莫名的|一种说不出的|一种莫名的)",
    "target_density": 0.3,
    "weight": 1.5,
    "severity": "medium",
    "description": "Chinese emotion tags",
    "suggestion": "Show through context"
  },
  {
    "name": "exposition_paragraphs_cn",
    "regex": "(?:说白了|也就是说|换句话说|其实|本质上|不得不说)",
    "target_density": 0.2,
    "weight": 2,
    "severity": "high",
    "description": "Chinese meta-exposition",
    "suggestion": "Delete the explanation"
  },
  {
    "name": "vague_quantity_cn",
    "regex": "(?:一些|某些|些许|若干|几分|一丝)",
    "target_density": 0.5,
    "weight": 0.5,
    "severity": "low",
    "description": "Chinese vague quantity",
    "suggestion": "Use specific numbers"
  }
]

CLUSTER_THRESHOLD = 3
CLUSTER_WINDOW_WORDS = 150
SEVERITY_CRITICAL = 12.0
SEVERITY_HIGH = 6.0
SEVERITY_MEDIUM = 3.0

def find_pattern_matches(text, regex):
    matches = []
    lines = text.split("\n")
    line_offsets = []
    offset = 0
    for line in lines: line_offsets.append(offset); offset += len(line) + 1
    for m in re.finditer(regex, text):
        pos = m.start(); line_num = len(line_offsets) - 1
        for i, lo in enumerate(line_offsets):
            if lo > pos: line_num = max(0, i - 1); break
        cs = max(0, line_num - 1); ce = min(len(lines), line_num + 2)
        ctx = "\n".join(lines[cs:ce])
        matches.append({"pattern": None, "line": line_num + 1, "match": m.group(0), "context": ctx[:300], "char_pos": pos})
    return matches

def detect_clusters(text, all_matches):
    tokens = list(re.finditer(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", text))
    wps = [match.start() for match in tokens]
    def ctow(cp):
        for i, wp in enumerate(wps):
            if wp >= cp: return max(0, i - 1)
        return max(0, len(wps) - 1)
    cl = []; bp = {}
    for m in all_matches: bp.setdefault(m["pattern"], []).append(m)
    for pn, ms in bp.items():
        if len(ms) < CLUSTER_THRESHOLD: continue
        sm = sorted(ms, key=lambda x: x["char_pos"])
        for i in range(len(sm) - CLUSTER_THRESHOLD + 1):
            ws = sm[i]; we = sm[i + CLUSTER_THRESHOLD - 1]
            sw = ctow(ws["char_pos"]); ew = ctow(we["char_pos"])
            if ew - sw <= CLUSTER_WINDOW_WORDS:
                c = 0
                for m in sm[i:]:
                    if ctow(m["char_pos"]) - sw <= CLUSTER_WINDOW_WORDS: c += 1
                    else: break
                cl.append({"pattern": pn, "count": c, "window_words": ew - sw, "start_line": ws["line"], "end_line": we["line"], "context": ws["context"]})
    seen = set(); uq = []
    for c in sorted(cl, key=lambda x: -x["count"]):
        k = (c["pattern"], c["start_line"])
        if k not in seen: seen.add(k); uq.append(c)
    return uq

def detect_repeated_openings(text):
    paras = re.split("\n\\s*\n", text); issues = []
    for para in paras:
        para = para.strip()
        if not para or para.startswith("#"): continue
        sents = re.split("[.!?\u3002\uff01\uff1f]+", para)
        if len(sents) < 3: continue
        ops = []
        for s in sents:
            s = s.strip()
            if s:
                chinese = "".join(re.findall(r"[\u4e00-\u9fff]", s))
                ops.append(chinese[:4] if chinese else " ".join(s.split()[:3]).lower())
        for op, cnt in Counter(ops).items():
            if cnt >= 3:
                issues.append({"pattern": "repeated_opening", "count": cnt, "opener": op, "line": text[:text.find(para)].count("\n") + 1, "context": para[:200]})
    return issues

class WritingAnalyzer:
    def __init__(self, logger):
        self.logger = logger; self.patterns = FICTION_PATTERNS + CHINESE_EXTRAS
    def detect_patterns(self, text):
        if not text or not text.strip(): return {"word_count": 0, "patterns": [], "clusters": [], "repeated_openings": [], "severity_score": 0, "tier": "LOW"}
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin_words = len(re.findall(r"[A-Za-z0-9_]+", text))
        wc = chinese_chars + latin_words
        if wc == 0: return {"word_count": 0, "patterns": [], "clusters": [], "repeated_openings": [], "severity_score": 0, "tier": "LOW"}
        am = []; rs = []
        for p in self.patterns:
            n = p["name"]; rx = p["regex"]; tg = p["target_density"]; wt = p["weight"]
            ms = find_pattern_matches(text, rx)
            for m in ms: m["pattern"] = n
            cnt = len(ms); den = (cnt / wc) * 1000
            rs.append({"name": n, "count": cnt, "density": round(den, 2), "target_density": tg, "over_target": den > tg, "weight": wt, "severity": p.get("severity", "medium"), "description": p.get("description", ""), "suggestion": p.get("suggestion", ""), "flagged_lines": [{"line": m["line"], "match": m["match"], "context": m["context"][:120]} for m in ms[:10]] if ms else []})
            am.extend(ms)
        clusters = detect_clusters(text, am)
        ros = detect_repeated_openings(text)
        ss = 0
        for r in rs:
            if r["over_target"]: ss += (r["density"] - r["target_density"]) * r["weight"]
        ss += len(clusters) * 1.5 + len(ros) * 1.0
        if ss >= SEVERITY_CRITICAL: t = "CRITICAL"
        elif ss >= SEVERITY_HIGH: t = "HIGH"
        elif ss >= SEVERITY_MEDIUM: t = "MEDIUM"
        else: t = "LOW"
        return {"word_count": wc, "patterns": rs, "clusters": clusters, "repeated_openings": ros, "severity_score": round(ss, 1), "tier": t}
    def analyze_pacing(self, text):
        if not text or not text.strip(): return {"error": "empty text"}
        paras = [p for p in text.split("\n") if p.strip()]
        sl = re.split("[\u3002\uff01\uff1f.!?\n]+", text)
        sents = [s.strip() for s in sl if len(s.strip()) > 3]
        if not sents: return {"error": "no valid sentences"}
        pl = [len(p) for p in paras]; sl2 = [len(s) for s in sents]
        ap = sum(pl) / len(pl) if pl else 0; as2 = sum(sl2) / len(sl2) if sl2 else 0
        sd = (sum((x - as2)**2 for x in sl2) / len(sl2))**0.5 if len(sl2) > 1 else 0
        dc = sum(1 for p in paras if any(c in p for c in "\u201c\u201d\"\u300c\u300d"))
        dr = dc / len(paras) if paras else 0
        return {"total_chars": len(text), "paragraphs": len(paras), "sentences": len(sents), "avg_para_length": round(ap, 1), "avg_sentence_length": round(as2, 1), "sentence_std_dev": round(sd, 1), "short_paras_pct": round(sum(1 for p in pl if p < 30) / len(paras) * 100, 1) if paras else 0, "dialogue_ratio": round(dr * 100, 1)}
    def analyze_chapter(self, chapter_number, content, title=""):
        pr = self.detect_patterns(content); pacing = self.analyze_pacing(content)
        pns = {"high": 10, "medium": 5, "low": 2}
        pp = sum(min(p["weight"] * pns.get(p["severity"], 3), 30) for p in pr.get("patterns", []) if p["over_target"])
        cp = len(pr.get("clusters", [])) * 5; op = len(pr.get("repeated_openings", [])) * 3
        total = min(pp + cp + op, 100); qs = max(0, 100 - total)
        return {"chapter": chapter_number, "title": title, "quality_score": round(qs, 1), "tier": pr.get("tier", "LOW"), "severity_score": pr.get("severity_score", 0), "word_count": pr.get("word_count", 0), "total_issues": sum(1 for p in pr.get("patterns", []) if p["over_target"]), "high_severity": sum(1 for p in pr.get("patterns", []) if p["over_target"] and p["severity"] == "high"), "cluster_count": len(pr.get("clusters", [])), "repeated_openings_count": len(pr.get("repeated_openings", [])), "patterns": pr.get("patterns", []), "clusters": pr.get("clusters", []), "repeated_openings": pr.get("repeated_openings", []), "pacing": pacing}
