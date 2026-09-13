import { describe, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  addProject,
  bulletTextToLatex,
  escapeLatex,
  latexEditableText,
  latexText,
  insertBullet,
  parseResume,
  replaceBullet,
} from "../lib/resume-latex";

const template = readFileSync(
  new URL("../public/resume-template.tex", import.meta.url),
  "utf8",
);
const fixture = String.raw`\newcommand{\resumeItem}[1]{\item{#1}}
\begin{document}
\section{Projects}
\resumeSubHeadingListStart
\resumeProjectHeading{\textbf{Test} $|$ \emph{Python}}{2025}
\resumeItemListStart
% \resumeItem{not a real bullet}
\resumeItem{Built \textbf{nested {formatting}} with 20\% less \{noise\}.}
\resumeSubItem{Same text}
\resumeItem{Same text}
\resumeItemListEnd
\resumeSubHeadingListEnd
\end{document}`;

describe("source-preserving LaTeX edits", () => {
  test("starter exposes experience and project bullets, not command definitions", () => {
    const result = parseResume(template);
    assert.equal(result.bullets.length, 2);
    assert.equal(result.groups.length, 2);
  });
  test("nested braces, escapes, comments, and repeated bullets retain exact spans", () => {
    const { bullets } = parseResume(fixture);
    assert.equal(bullets.length, 3);
    assert.ok(
      bullets[0].latex.includes(String.raw`\textbf{nested {formatting}}`),
    );
    const result = replaceBullet(
      fixture,
      bullets[2],
      "Cut errors by 20% & shipped",
    );
    assert.ok(result.includes(String.raw`\resumeSubItem{Same text}`));
    assert.ok(
      result.includes(String.raw`\resumeItem{Cut errors by 20\% \& shipped}`),
    );
    assert.equal(
      result.slice(0, bullets[2].start),
      fixture.slice(0, bullets[2].start),
    );
  });
  test("rejects stale span replacement", () => {
    const bullet = parseResume(fixture).bullets[0];
    assert.throws(() => replaceBullet("changed" + fixture, bullet, "New text"));
  });
  test("insertions remain in the selected list and project block", () => {
    const first = insertBullet(
      fixture,
      parseResume(fixture).groups[0].id,
      "Added & tested",
    );
    assert.equal(parseResume(first).bullets.length, 4);
    const second = addProject(first, "New & Better", "2026", "TypeScript");
    assert.equal(parseResume(second).groups.length, 2);
    assert.ok(second.includes(String.raw`\textbf{New \& Better}`));
  });
  test("unsupported and incomplete documents do not expose unsafe edits", () => {
    assert.deepEqual(parseResume(String.raw`\resumeItem{oops}`).bullets, []);
    assert.ok(
      parseResume(fixture.replace("{Same text}", "{unclosed")).bullets.length <
        3,
    );
    assert.throws(() => addProject("no sections", "Title", "", ""));
  });
  test("escapes generated text literally", () => {
    assert.equal(
      escapeLatex(String.raw`\input{secret} $5 #1 ~ ^`),
      String.raw`\textbackslash{}input\{secret\} \$5 \#1 \textasciitilde{} \textasciicircum{}`,
    );
  });
  test("plain-text editing preserves escaped characters and excludes comments", () => {
    const text =
      "Built {an API} for $5 & reduced errors 20% using C#_tools \\ ~ ^";
    assert.equal(latexText(escapeLatex(text)), text);
    assert.equal(latexText("Visible % hidden comment\ntext"), "Visible text");
  });
  test("bold editing round-trips through safe LaTeX", () => {
    assert.equal(
      latexEditableText(String.raw`Built \textbf{secure APIs} with Go`),
      "Built **secure APIs** with Go",
    );
    assert.equal(
      bulletTextToLatex("Built **secure APIs** with Go & TypeScript"),
      String.raw`Built \textbf{secure APIs} with Go \& TypeScript`,
    );
    assert.equal(bulletTextToLatex("Keep ** unmatched"), "Keep ** unmatched");
  });
  test("the first accepted project bullet replaces the explicit placeholder", () => {
    const next = addProject(template, "My Project", "2026", "Python");
    const group = parseResume(next).groups.at(-1)!;
    const inserted = insertBullet(next, group.id, "Built a tested API");
    assert.ok(!inserted.includes("Add your contribution here."));
    assert.ok(inserted.includes("Built a tested API"));
  });
});
