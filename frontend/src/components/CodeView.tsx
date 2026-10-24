import { useEffect, useMemo, useRef } from "react";

const KEYWORDS = new Set(
  (
    "abstract async await break case catch class const continue def default defer delete do elif else enum export extends false final finally fn for from func function " +
    "go if impl implements import in instanceof interface is lambda let match module mut namespace new nil none null package pass private protected pub public raise readonly " +
    "return self static struct super switch this throw throws trait true try type typeof use using val var void when where while with yield None True False and or not " +
    "select from where insert update delete into values join create table procedure begin end perform move display call copy"
  ).split(" "),
);

type Token = { text: string; cls: string };

function tokenize(line: string, state: { block: boolean }): Token[] {
  const tokens: Token[] = [];
  let index = 0;
  const push = (text: string, cls = "") => text && tokens.push({ text, cls });
  while (index < line.length) {
    if (state.block) {
      const end = line.indexOf("*/", index);
      if (end === -1) {
        push(line.slice(index), "tok-c");
        return tokens;
      }
      push(line.slice(index, end + 2), "tok-c");
      index = end + 2;
      state.block = false;
      continue;
    }
    const rest = line.slice(index);
    if (rest.startsWith("/*")) {
      state.block = true;
      continue;
    }
    if (rest.startsWith("//") || /^#(?!include|define|\[)/.test(rest) || rest.startsWith("--")) {
      push(rest, "tok-c");
      return tokens;
    }
    const string = /^("(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|`(?:\\.|[^`\\])*`?)/.exec(rest);
    if (string) {
      push(string[0], "tok-s");
      index += string[0].length;
      continue;
    }
    const annotation = /^@[\w.]+/.exec(rest);
    if (annotation) {
      push(annotation[0], "tok-a");
      index += annotation[0].length;
      continue;
    }
    const number = /^\d[\d_.xXa-fA-F]*/.exec(rest);
    if (number) {
      push(number[0], "tok-n");
      index += number[0].length;
      continue;
    }
    const word = /^[A-Za-z_$][\w$]*/.exec(rest);
    if (word) {
      const after = rest.slice(word[0].length);
      const cls = KEYWORDS.has(word[0]) ? "tok-k" : /^\s*\(/.test(after) ? "tok-f" : "";
      push(word[0], cls);
      index += word[0].length;
      continue;
    }
    push(rest[0]);
    index += 1;
  }
  return tokens;
}

/** Read-only code view with line numbers, light syntax colouring and an active line range. */
export function CodeView({ content, highlight, scrollTo }: { content: string; highlight?: [number, number] | null; scrollTo?: number | null }) {
  const container = useRef<HTMLDivElement>(null);
  const lines = useMemo(() => {
    const state = { block: false };
    return content.split("\n").slice(0, 20000).map((line) => tokenize(line, state));
  }, [content]);

  useEffect(() => {
    if (!scrollTo || !container.current) return;
    const row = container.current.querySelector(`[data-line="${scrollTo}"]`);
    row?.scrollIntoView({ block: "center" });
  }, [scrollTo, content]);

  return (
    <div ref={container} className="code-view h-full overflow-auto bg-inset py-2">
      <table className="border-collapse">
        <tbody>
          {lines.map((tokens, index) => {
            const number = index + 1;
            const active = highlight && number >= highlight[0] && number <= highlight[1];
            return (
              <tr key={number} data-line={number} className={active ? "active" : ""}>
                <td className="ln tnum align-top">{number}</td>
                <td className="whitespace-pre pr-6">
                  {tokens.length ? tokens.map((token, i) => (token.cls ? <span key={i} className={token.cls}>{token.text}</span> : token.text)) : " "}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
