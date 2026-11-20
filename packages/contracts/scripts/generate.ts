/**
 * M02 §4.1.2 生成接口 —— generate.ts
 * 输入只有 packages/contracts（单一事实来源），输出全部生成物（I3 hash 受 CI 校验）。
 *  - gen:openapi : openapi-typescript → packages/sdk/ts/types.ts
 *  - gen:zod     : JSON Schema → packages/sdk/ts/schemas.ts（Zod 运行时校验）
 *  - gen:enums   : enums.source.json → enums.ts / enums.py（前后端同源，I2）
 * 生成幂等：同输入同输出（§4.5）。
 */
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = resolve(dirname(fileURLToPath(import.meta.url)), ".."); // packages/contracts
const SDK_TS = resolve(HERE, "../sdk/ts");

/** Windows 兼容：pnpm/cmd 需经 shell 解析（execFileSync 直接 spawn 会 ENOENT） */
function runPnpm(args: string[], cwd: string): void {
  const r = spawnSync("pnpm", args, { stdio: "inherit", cwd, shell: process.platform === "win32" });
  if (r.status !== 0) throw new Error(`command failed: pnpm ${args.join(" ")}`);
}

// ---------- gen:openapi ----------
export function generateTsTypes(specPath: string, outPath: string): void {
  runPnpm(["exec", "openapi-typescript", resolve(HERE, specPath), "-o", resolve(HERE, outPath)], HERE);
  // openapi-typescript 只输出 components/paths 命名空间；客户端用的 ApiResponse<T>
  // 别名必须在生成阶段补上，否则 packages/sdk/ts/client.ts 导入不存在的符号。
  const target = resolve(HERE, outPath);
  const generated = readFileSync(target, "utf8");
  const alias =
    '\nexport type ApiResponse<T = unknown> = components["schemas"]["ApiResponse"] & { data?: T };\n';
  if (!generated.includes("export type ApiResponse<")) {
    writeFileSync(target, generated + alias);
  }
}

// ---------- gen:zod（JSON Schema → Zod，紧凑递归发射器）----------
function zodOf(node: any, name?: string): string {
  if (node.$ref) {
    const ref = String(node.$ref);
    // 跨文件 $ref 形如 "widget_spec.schema.json"——须映射为生成物变量名，
    // 直接拼出 Schemas.widget_spec.schema.json 会产生非法 TypeScript。
    const crossFile: Record<string, string> = {
      "widget_spec.schema.json": "WidgetSpec",
      "widget_bundle.schema.json": "WidgetBundle",
      "widget_runtime.schema.json": "WidgetRuntime",
      "events.schema.json": "AgentEvent",
    };
    const tail = ref.split("/").pop()!;
    return crossFile[tail] ?? "z.unknown()";
  }
  if (node.enum && node.meta === "string") return `z.enum(${JSON.stringify(node.enum)})`;
  if (node.enum) return `z.enum([${node.enum.map((v: any) => JSON.stringify(v)).join(",")}])`;
  // z.literal 只接受 primitive；契约里的 const 数组（import_allowlist / sandbox /
  // bridge_messages）必须用 tuple of literals，生成物才能通过 tsc 且保留精确值。
  if ("const" in node) {
    if (Array.isArray(node.const)) {
      return `z.tuple([${node.const.map((v: any) => `z.literal(${JSON.stringify(v)})`).join(",")}])`;
    }
    return `z.literal(${JSON.stringify(node.const)})`;
  }
  switch (node.type) {
    case "string": {
      let z = "z.string()";
      if (node.format === "date-time") z += ".datetime({ offset: true })";
      if (node.format === "email") z += ".email()";
      if (typeof node.minLength === "number") z += `.min(${node.minLength})`;
      return z;
    }
    case "integer":
    case "number": {
      let z = node.type === "integer" ? "z.number().int()" : "z.number()";
      if (typeof node.minimum === "number") z += `.min(${node.minimum})`;
      return z;
    }
    case "boolean": return "z.boolean()";
    case "array": return `z.array(${zodOf(node.items)})`;
    case "object": {
      if (!node.properties) return "z.record(z.unknown())";
      const fields = Object.entries(node.properties).map(([k, v]) => {
        const req = (node.required ?? []).includes(k);
        const inner = zodOf(v);
        return `  ${JSON.stringify(k)}: ${req ? inner : `${inner}.optional()`},`;
      });
      const addl = node.additionalProperties === false ? "" : ".passthrough()";
      return `z.object({\n${fields.join("\n")}\n})${addl}`;
    }
    default: return "z.unknown()";
  }
}

export function generateZodSchemas(schemaDir: string, outPath: string): void {
  const files = [
    ["AgentEvent", "events.schema.json"],
    ["WidgetSpec", "widget_spec.schema.json"],
    ["WidgetBundle", "widget_bundle.schema.json"],
    ["WidgetRuntime", "widget_runtime.schema.json"],
  ] as const;
  const chunks: string[] = [
    "// M02 生成物：事件/三协议 Zod 运行时校验 schema（禁止手改）",
    'import { z } from "zod";',
    "",
  ];
  // 先生成独立 const，再聚合 Schemas：若把相互引用直接写进同一个对象字面量，
  // tsc 会报 TS7022（变量在自身初始化中被间接引用）。
  for (const [name, file] of files) {
    const schema = JSON.parse(readFileSync(resolve(schemaDir, file), "utf8"));
    chunks.push(`const ${name} = ${zodOf(schema)};`, "");
  }
  chunks.push(
    `export const Schemas = { ${files.map(([name]) => name).join(", ")} } as const;`,
    "",
  );
  // 具名导出（M02 §4.3 契约：AgentEventSchema 等）
  for (const [name] of files) {
    chunks.push(`export const ${name}Schema = Schemas.${name};`);
  }
  chunks.push("");
  writeFileSync(resolve(schemaDir, outPath), chunks.join("\n"));
}

// ---------- gen:enums（enums.ts / enums.py 同源，I2）----------
function pyConst(v: any): string {
  if (Array.isArray(v)) return "(" + v.map(pyConst).join(", ") + (v.length === 1 ? "," : "") + ")";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}
function snake(s: string): string {
  let out = s.replace(/[^A-Za-z0-9]+/g, "_").replace(/([a-z0-9])([A-Z])/g, "$1_$2").toUpperCase();
  // Python 标识符不能以数字开头（如 GridSpec 的 "2x2" → "2X2" 非法），
  // 补前导下划线生成合法成员名（值集合不变，契约语义不受影响）。
  if (/^[0-9]/.test(out)) out = "_" + out;
  return out;
}

export function generateEnums(srcPath: string, tsOut: string, pyOut: string): void {
  const src = JSON.parse(readFileSync(resolve(HERE, srcPath), "utf8"));
  const names = Object.keys(src).filter((k) => !k.startsWith("$"));
  // TS
  const ts: string[] = [
    "// M02 enums.ts —— 自动生成，禁止手改（单一事实来源 enums.source.json）",
    "// 同源产物：enums.ts / enums.py 值集合必须完全一致（I2，CI contracts:check 校验）",
    "",
  ];
  for (const n of names) {
    ts.push(`export type ${n} =`);
    ts.push(`  | ${(src[n] as string[]).map((v) => JSON.stringify(v)).join(" | ")};`);
    ts.push("");
  }
  const consts = src.$constants ?? {};
  for (const [k, v] of Object.entries(consts)) {
    if (k.startsWith("$")) continue;
    ts.push(`export const ${k} = ${JSON.stringify(v)} as const;`);
  }
  ts.push("");
  writeFileSync(resolve(HERE, tsOut), ts.join("\n"));
  // PY
  const py: string[] = [
    "# M02 enums.py —— 自动生成，禁止手改（单一事实来源 enums.source.json）",
    "# 同源产物：enums.ts / enums.py 值集合必须完全一致（I2，CI contracts:check 校验）",
    "from __future__ import annotations",
    "",
    "from enum import StrEnum",
    "",
    "",
  ];
  for (const n of names) {
    py.push(`class ${n}(StrEnum):`);
    for (const v of src[n] as string[]) py.push(`    ${snake(v)} = ${JSON.stringify(v)}`);
    py.push("");
  }
  for (const [k, v] of Object.entries(consts)) {
    if (k.startsWith("$")) continue;
    py.push(`${k} = ${pyConst(v)}`);
  }
  py.push("");
  writeFileSync(resolve(HERE, pyOut), py.join("\n"));
}

// ---------- CLI ----------
const cmd = process.argv[2];
if (cmd === "gen:openapi") {
  generateTsTypes("openapi.yaml", "../sdk/ts/types.ts");
  console.log("gen:openapi OK → packages/sdk/ts/types.ts");
} else if (cmd === "gen:zod") {
  generateZodSchemas(HERE, "../sdk/ts/schemas.ts");
  console.log("gen:zod OK → packages/sdk/ts/schemas.ts");
} else if (cmd === "gen:enums") {
  generateEnums("enums.source.json", "enums.ts", "enums.py");
  console.log("gen:enums OK → enums.ts + enums.py");
} else if (cmd === "gen:all") {
  generateEnums("enums.source.json", "enums.ts", "enums.py");
  generateZodSchemas(HERE, "../sdk/ts/schemas.ts");
  try { generateTsTypes("openapi.yaml", "../sdk/ts/types.ts"); }
  catch { console.log("WARN: openapi-typescript 未安装，跳过 TS 类型生成（contracts:check 不含该项）"); }
  console.log("gen:all OK");
} else {
  console.error("用法: tsx scripts/generate.ts {gen:openapi|gen:zod|gen:enums|gen:all}");
  process.exit(1);
}
