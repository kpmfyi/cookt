// Word-level diff for the Review page: an LCS over word/punctuation tokens.

export interface DiffPart {
  type: "same" | "del" | "ins";
  text: string;
}

function tokens(text: string): string[] {
  return text.match(/\s+|[\p{L}\p{N}'’½¼¾⅓⅔⅛°]+|[^\s\p{L}\p{N}]/gu) ?? [];
}

export function diffWords(before: string, after: string): DiffPart[] {
  const a = tokens(before);
  const b = tokens(after);
  // lcs[i][j] = LCS length of a[i:] and b[j:]
  const lcs = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const parts: DiffPart[] = [];
  const push = (type: DiffPart["type"], text: string) => {
    const last = parts.at(-1);
    if (last && last.type === type) last.text += text;
    else parts.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push("same", a[i]);
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      push("del", a[i++]);
    } else {
      push("ins", b[j++]);
    }
  }
  while (i < a.length) push("del", a[i++]);
  while (j < b.length) push("ins", b[j++]);
  // Whitespace alone between two changes reads better inside the change.
  return parts.flatMap((part, k) => {
    const prev = parts[k - 1];
    const next = parts[k + 1];
    if (part.type === "same" && !part.text.trim() && prev && next && prev.type !== "same" && next.type !== "same") {
      return [{ type: "del" as const, text: part.text }, { type: "ins" as const, text: part.text }];
    }
    return [part];
  });
}
