import postcss from "postcss";
import tailwindcss from "tailwindcss";
import { beforeAll, describe, expect, it } from "vitest";

// Issue #49: every class a component uses is defined (authored stylesheet or Tailwind
// utility), every stylesheet is imported, and every design token has a reader. A class nobody
// defines silently does nothing; this is how `muted`, `form-hint` and `btn--secondary` went
// unnoticed (DESIGN.md "Global classes").
const sources = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
});
const stylesheets = import.meta.glob<string>("../**/*.css", { query: "?raw", import: "default", eager: true });
const allSource = Object.values(sources).join("\n");

// Run from frontend/ like `npm test`: Tailwind reads tailwind.config.cjs and its content globs from there.
// Classes Tailwind or the browser never define on their own: variant anchors.
const markers = new Set(["group", "peer"]);

const classPattern = /\.(-?(?:[_a-zA-Z]|\\.)(?:[\w-]|\\.)*)/g;

function selectorClasses(selector: string): string[] {
  return [...selector.matchAll(classPattern)].map((match) => match[1].replace(/\\(.)/g, "$1"));
}

// Strings compared with `===` are values, not classes.
const compared = /[!=]==?\s*(?:"[^"\n]*"|'[^'\n]*')|(?:"[^"\n]*"|'[^'\n]*')\s*[!=]==?/g;

function literals(expression: string): string[] {
  const found: string[] = [];
  for (const match of expression.replace(compared, " ").matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)) {
    const text = match[1] ?? match[2] ?? match[3] ?? "";
    found.push(text.replace(/\$\{[^}]*\}/g, " "));
    for (const inner of text.matchAll(/\$\{([^}]*)\}/g)) found.push(...literals(inner[1]));
  }
  return found;
}

// Static class tokens of every `className=` attribute: a plain string, or the string literals
// inside a `{...}` expression. Tokens built from an interpolation are not checkable.
function usedClasses(): Map<string, string[]> {
  const used = new Map<string, string[]>();
  for (const [path, text] of Object.entries(sources)) {
    for (const attribute of text.matchAll(/className\s*=\s*/g)) {
      const start = attribute.index + attribute[0].length;
      let expression: string[];
      if (text[start] === '"' || text[start] === "'") {
        expression = [text.slice(start + 1, text.indexOf(text[start], start + 1))];
      } else if (text[start] === "{") {
        let depth = 0;
        let end = start;
        for (; end < text.length; end++) {
          if (text[end] === "{") depth++;
          if (text[end] === "}" && --depth === 0) break;
        }
        expression = literals(text.slice(start + 1, end));
      } else continue;
      for (const token of expression.join(" ").split(/\s+/)) {
        if (!token || token.endsWith("-")) continue;
        used.set(token, [...(used.get(token) ?? []), path]);
      }
    }
  }
  return used;
}

const defined = new Set<string>();
const declared = new Set<string>();
const referenced = new Set<string>();

beforeAll(async () => {
  for (const [path, css] of Object.entries(stylesheets)) {
    const authored = postcss.parse(css);
    authored.walkDecls(/^--/, (declaration) => void declared.add(declaration.prop));
    const compiled = css.includes("@tailwind") ? (await postcss([tailwindcss("./tailwind.config.cjs")]).process(css, { from: path })).root : authored;
    compiled.walkRules((rule) => selectorClasses(rule.selector).forEach((name) => defined.add(name)));
    compiled.walkDecls((declaration) => {
      for (const match of declaration.value.matchAll(/var\(\s*(--[\w-]+)/g)) referenced.add(match[1]);
    });
  }
  for (const match of allSource.matchAll(/var\(\s*(--[\w-]+)/g)) referenced.add(match[1]);
}, 60_000);

describe("style hygiene", () => {
  it("scans the application sources and stylesheets", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(100);
    expect(Object.keys(stylesheets).some((path) => path.endsWith("/index.css"))).toBe(true);
    // `btn--primary` is authored CSS; the second class is a Tailwind utility spelled out here only in pieces.
    expect(defined.has("btn--primary") && defined.has(["whitespace", "nowrap"].join("-"))).toBe(true);
  });

  it("uses only classes that a stylesheet or Tailwind defines", () => {
    const undefinedClasses = [...usedClasses()]
      .filter(([token]) => !defined.has(token) && !markers.has(token))
      .map(([token, paths]) => `${token} (${[...new Set(paths)].map((path) => path.replace("../", "")).join(", ")})`);
    expect(undefinedClasses).toEqual([]);
  });

  it("writes legacy component modifiers with two hyphens", () => {
    const typos = [...usedClasses().keys()].filter((token) => /^(btn|badge)-[a-z]/.test(token));
    expect(typos).toEqual([]);
  });

  it("imports every stylesheet", () => {
    const orphans = Object.keys(stylesheets).filter((path) => {
      const file = path.slice(path.lastIndexOf("/") + 1);
      return !new RegExp(`import\\s+["'][^"']*${file.replace(".", "\\.")}["']`).test(allSource);
    });
    expect(orphans).toEqual([]);
  });

  it("declares only design tokens that something reads", () => {
    expect([...declared].filter((token) => !referenced.has(token)).sort()).toEqual([]);
  });
});
