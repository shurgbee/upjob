export type ResumeBullet = {
  id: string;
  start: number;
  end: number;
  latex: string;
  text: string;
  editableText: string;
  group: string;
};
export type ResumeGroup = { id: string; title: string; insertion: number };

// Mask comments without changing offsets; escaped percent signs are literal.
function maskComments(source: string) {
  return source.replace(
    /(\\[\s\S])|%[^\n]*/g,
    (match, escaped) => escaped ?? " ".repeat(match.length),
  );
}

function argument(
  source: string,
  from: number,
): { start: number; end: number; next: number } | null {
  let i = from;
  while (/\s/.test(source[i] ?? "") && i < source.length) i++;
  if (source[i] !== "{") return null;
  const start = ++i;
  let depth = 1;
  for (; i < source.length; i++) {
    if (source[i] === "\\") {
      i++;
      continue;
    }
    if (source[i] === "{") depth++;
    if (source[i] === "}" && --depth === 0)
      return { start, end: i, next: i + 1 };
  }
  return null;
}

export function latexText(latex: string): string {
  const literals: string[] = [];
  const protect = (value: string) => `\uE000${literals.push(value) - 1}\uE001`;
  return maskComments(latex)
    .replace(
      /\\(textbackslash|textasciitilde|textasciicircum)\{\}/g,
      (_, command) =>
        protect(
          (
            {
              textbackslash: "\\",
              textasciitilde: "~",
              textasciicircum: "^",
            } as Record<string, string>
          )[command],
        ),
    )
    .replace(/\\([%&#_${}])/g, (_, value) => protect(value))
    .replace(/\\(?:textbf|textit|emph|underline|small|textnormal)\b/g, "")
    .replace(
      /\\(?:href|url)\{([^{}]*)\}(?:\{([^{}]*)\})?/g,
      (_, url, text) => text ?? url,
    )
    .replace(/[{}$]/g, "")
    .replace(/\uE000(\d+)\uE001/g, (_, index) => literals[Number(index)])
    .replace(/\s+/g, " ")
    .trim();
}

export function latexEditableText(latex: string): string {
  const bold = /\\textbf\b/g;
  let marked = "";
  let cursor = 0;
  for (let match; (match = bold.exec(latex)); ) {
    const arg = argument(latex, match.index + match[0].length);
    if (!arg) continue;
    marked += latex.slice(cursor, match.index);
    marked += `**${latexText(latex.slice(arg.start, arg.end))}**`;
    cursor = arg.next;
    bold.lastIndex = arg.next;
  }
  return latexText(marked + latex.slice(cursor));
}

export function parseResume(source: string): {
  bullets: ResumeBullet[];
  groups: ResumeGroup[];
} {
  const masked = maskComments(source);
  const begin = masked.indexOf("\\begin{document}");
  const end = masked.indexOf("\\end{document}", begin);
  if (begin < 0) return { bullets: [], groups: [] };
  const body = masked.slice(begin, end < 0 ? undefined : end);
  const commands =
    /\\(resumeItem|resumeSubItem|resumeProjectHeading|resumeSubheading|resumeSubSubheading|resumeItemListStart|resumeItemListEnd|section)\b/g;
  const bullets: ResumeBullet[] = [];
  const groups: ResumeGroup[] = [];
  let title = "Resume";
  let group = "";
  let inList = false;
  for (let match; (match = commands.exec(body));) {
    const command = match[1];
    const offset = begin + match.index;
    if (command === "resumeItemListStart") {
      inList = true;
      continue;
    }
    if (command === "resumeItemListEnd") {
      if (group && inList) groups.push({ id: group, title, insertion: offset });
      inList = false;
      continue;
    }
    const arg = argument(masked, offset + match[0].length);
    if (!arg) continue;
    commands.lastIndex = arg.next - begin;
    if (command === "resumeItem" || command === "resumeSubItem") {
      if (!inList) continue;
      const latex = source.slice(arg.start, arg.end);
      bullets.push({
        id: String(arg.start),
        start: arg.start,
        end: arg.end,
        latex,
        text: latexText(latex),
        editableText: latexEditableText(latex),
        group,
      });
    } else {
      title = latexText(source.slice(arg.start, arg.end));
      group = `${offset}`;
    }
  }
  return { bullets, groups };
}

export function escapeLatex(text: string): string {
  const escapes: Record<string, string> = {
    "\\": "\\textbackslash{}",
    "&": "\\&",
    "%": "\\%",
    $: "\\$",
    "#": "\\#",
    _: "\\_",
    "{": "\\{",
    "}": "\\}",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
  };
  return text.replace(/[\\&%$#_{}~^]/g, (char) => escapes[char]);
}

export function bulletTextToLatex(text: string): string {
  const bold = /\*\*([^*\n]+?)\*\*/g;
  let latex = "";
  let cursor = 0;
  for (let match; (match = bold.exec(text)); ) {
    latex += escapeLatex(text.slice(cursor, match.index));
    latex += `\\textbf{${escapeLatex(match[1])}}`;
    cursor = match.index + match[0].length;
  }
  return latex + escapeLatex(text.slice(cursor));
}

export function replaceBullet(
  source: string,
  bullet: ResumeBullet,
  text: string,
) {
  if (source.slice(bullet.start, bullet.end) !== bullet.latex)
    throw new Error("This bullet has changed. Review it again.");
  return (
    source.slice(0, bullet.start) +
    bulletTextToLatex(text) +
    source.slice(bullet.end)
  );
}

export function insertBullet(source: string, groupId: string, text: string) {
  const parsed = parseResume(source);
  const group = parsed.groups.find((item) => item.id === groupId);
  if (!group)
    throw new Error("Select a recognized project or experience entry first.");
  const placeholder = parsed.bullets.find(
    (bullet) =>
      bullet.group === groupId &&
      bullet.latex === "Add your contribution here.",
  );
  if (placeholder) return replaceBullet(source, placeholder, text);
  return (
    source.slice(0, group.insertion) +
    `\\resumeItem{${bulletTextToLatex(text)}}\n    ` +
    source.slice(group.insertion)
  );
}

export function addProject(
  source: string,
  name: string,
  dates: string,
  technologies: string,
) {
  const masked = maskComments(source);
  const section = /\\section\s*\{Projects\}/i.exec(masked);
  if (!section)
    throw new Error("Add a Projects section in the LaTeX tab first.");
  const start = section.index + section[0].length;
  const end = masked.indexOf("\\resumeSubHeadingListEnd", start);
  const nextSection = masked.indexOf("\\section", start);
  if (end < 0 || (nextSection >= 0 && nextSection < end))
    throw new Error(
      "Projects must use Jake's resumeSubHeadingListStart/End commands.",
    );
  const block = `\\resumeProjectHeading{\\textbf{${escapeLatex(name)}} $|$ \\emph{${escapeLatex(technologies)}}}{${escapeLatex(dates)}}\n    \\resumeItemListStart\n      \\resumeItem{Add your contribution here.}\n    \\resumeItemListEnd\n  `;
  return source.slice(0, end) + block + source.slice(end);
}
