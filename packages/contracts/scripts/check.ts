/**
 * M02 §4.1.2 / §3.4B —— check.ts：CI 一致性校验（contracts:check）
 * 重新生成全部产物 → 与仓库已提交生成物做确定性 hash 比对 → drift 非空即非零退出。
 * 「schema 变更即失败」（§8 W6 行）；fail-closed，无在线关闭开关。
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../.."); // 仓库根
const CONTRACTS = resolve(ROOT, "packages/contracts");

function runGen(args: string[]): void {
  const r = spawnSync("pnpm", ["exec", "tsx", "scripts/generate.ts", ...args], {
    stdio: "pipe", cwd: CONTRACTS, shell: process.platform === "win32",
  });
  if (r.status !== 0) throw new Error(`generate failed: ${args.join(" ")}`);
}

/** 排序后文件内容摘要（确定性，§4.5 并发约定） */
export function hashGenerated(dir: string, relPrefix = ""): string {
  const h = createHash("sha256");
  const walk = (d: string, rel: string) => {
    for (const f of readdirSync(d).sort()) {
      const p = join(d, f);
      const r = rel ? `${rel}/${f}` : f;
      if (statSync(p).isDirectory()) walk(p, r);
      else h.update(`${r}:${createHash("sha256").update(readFileSync(p)).digest("hex")}\n`);
    }
  };
  walk(dir, relPrefix);
  return h.digest("hex");
}

export async function verifyGenerated(): Promise<{ ok: boolean; drift: string[] }> {
  const drift: string[] = [];

  // 1. 枚举一致性（I2）：enums.ts / enums.py 从同一 source 重新生成后逐字节比对
  const enumTs = readFileSync(join(CONTRACTS, "enums.ts"));
  const enumPy = readFileSync(join(CONTRACTS, "enums.py"));
  runGen(["gen:enums"]);
  if (!enumTs.equals(readFileSync(join(CONTRACTS, "enums.ts"))) || !enumPy.equals(readFileSync(join(CONTRACTS, "enums.py")))) {
    drift.push("enums: enums.source.json 与已提交 enums.ts/enums.py 不一致 → 运行 pnpm gen:enums");
  }

  // 2. Zod schema：重新生成与已提交 packages/sdk/ts/schemas.ts 比对（I3）
  const committedSchemas = readFileSync(resolve(ROOT, "packages/sdk/ts/schemas.ts"));
  runGen(["gen:zod"]);
  if (!committedSchemas.equals(readFileSync(resolve(ROOT, "packages/sdk/ts/schemas.ts")))) {
    drift.push("zod: schema 源与 packages/sdk/ts/schemas.ts 不一致 → 运行 pnpm gen:zod");
  }

  // 3. TS 类型（M02 §8.4 必需检查，fail-closed）：
  //    文件缺失 / 生成失败 / 生成物不一致一律计入 drift，使进程非零退出；不再静默跳过。
  try {
    const committedTypes = readFileSync(resolve(ROOT, "packages/sdk/ts/types.ts"));
    runGen(["gen:openapi"]);
    if (!committedTypes.equals(readFileSync(resolve(ROOT, "packages/sdk/ts/types.ts")))) {
      drift.push("types: openapi.yaml 与 packages/sdk/ts/types.ts 不一致 → 运行 pnpm gen:openapi");
    }
  } catch (error) {
    drift.push(`types: TS 类型校验失败（文件缺失或 openapi-typescript 生成失败，fail-closed）→ ${String(error)}`);
  }

  // 4. Python SDK 生成物（M02 §4.1.2）：此前门禁只校验 enums/zod/types，
  //    python 产物曾带着非法注解（widget_spec.schema.json）和缺失的 ApiResponse
  //    静默通过——此处同样 fail-closed，变更契约后必须 pnpm gen:python。
  try {
    const pyPaths = [
      resolve(ROOT, "packages/sdk/python/widgetpilot_client/__init__.py"),
      resolve(ROOT, "packages/sdk/python/widgetpilot_client/client.py"),
      resolve(ROOT, "packages/sdk/python/widgetpilot_client/models.py"),
    ];
    const committed = pyPaths.map((p) => readFileSync(p));
    const gen = spawnSync("python", ["scripts/gen_python.py"], {
      stdio: "pipe", cwd: CONTRACTS, shell: process.platform === "win32",
    });
    if (gen.status !== 0) {
      drift.push(`python-sdk: 生成失败 → ${String(gen.stderr).slice(0, 300)}`);
    } else if (pyPaths.some((p, i) => !committed[i].equals(readFileSync(p)))) {
      drift.push("python-sdk: openapi/schema 与 Python 生成物不一致 → 运行 pnpm gen:python");
    }
  } catch (error) {
    drift.push(`python-sdk: Python 生成物校验失败（文件缺失或生成异常，fail-closed）→ ${String(error)}`);
  }

  // 5. ApiResponse 字段镜像：生成器 _API_RESPONSE_FIELDS 是 openapi.yaml 的手写镜像，
  //    仅比对「生成物==生成器」不足以防字段漂移（schema 改字段 CI 不报）。此处按
  //    openapi.yaml 的 ApiResponse.properties 顶层键集合做 fail-closed 校验。
  try {
    const spec = readFileSync(resolve(CONTRACTS, "openapi.yaml"), "utf8").split(/\r?\n/);
    const start = spec.findIndex((l) => /^    ApiResponse:\s*$/.test(l));
    if (start < 0) {
      drift.push("api-response: openapi.yaml 缺少 components.schemas.ApiResponse");
    } else {
      const propLine = spec.findIndex((l, i) => i > start && /^      properties:\s*$/.test(l));
      const names = new Set<string>();
      for (const line of spec.slice(propLine + 1)) {
        if (line.trim() === "") continue;
        // 属性键为 8 空格缩进；6 空格或更少表示离开 ApiResponse.properties。
        if (!/^ {8,}\S/.test(line)) break;
        const m = /^ {8}([A-Za-z][A-Za-z0-9]*):/.exec(line);
        if (m) names.add(m[1]); // 子属性（10+ 空格）忽略
      }
      const genSrc = readFileSync(resolve(CONTRACTS, "scripts/gen_python.py"), "utf8");
      const mirror = new Set<string>();
      const fieldRe = /^\s*\("([A-Za-z][A-Za-z0-9]*)",\s*"(?:str|bool|JsonValue|int|float)"/gm;
      for (const m of genSrc.matchAll(fieldRe)) mirror.add(m[1]);
      const missing = [...names].filter((n) => !mirror.has(n));
      const extra = [...mirror].filter((n) => !names.has(n));
      if (missing.length || extra.length) {
        drift.push(
          `api-response: 生成器镜像字段与 openapi.yaml 不一致（缺 ${missing.join(",")} / 多 ${extra.join(",")}）→ 同步 gen_python.py:_API_RESPONSE_FIELDS`,
        );
      }
    }
  } catch (error) {
    drift.push(`api-response: 字段镜像校验失败（fail-closed）→ ${String(error)}`);
  }

  return { ok: drift.length === 0, drift };
}

// ---------- CLI ----------
const result = await verifyGenerated();
if (result.ok) {
  console.log("contracts:check OK —— 生成物与契约源一致（drift=[]）");
  process.exit(0);
} else {
  console.error("contracts:check FAILED —— schema 变更即失败（§8 W6）：");
  for (const d of result.drift) console.error(`  - ${d}`);
  process.exit(1);
}
