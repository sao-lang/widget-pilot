// M02 生成物：事件/三协议 Zod 运行时校验 schema（禁止手改）
import { z } from "zod";

export const Schemas = {
  AgentEvent: z.object({
  "seq": z.number().int().min(0),
  "sessionId": z.string().min(1),
  "runId": z.string().min(1),
  "ts": z.string().datetime({ offset: true }),
  "kind": z.enum(["run","plan","spec","attachment","skill","routing","code","guard","validation","sandbox","artifact","cost","checkpoint","interrupted","notice","error","done","ask_user"]),
  "phase": z.enum(["DRAFT","RUNNING","PAUSED","AWAITING_INPUT","VALIDATING","COMPLETED","FAILED","CANCELLED"]),
  "payload": z.unknown(),
  "traceId": z.string().min(1),
}),
  WidgetSpec: z.object({
  "spec_version": z.string().min(1),
  "widget_id": z.string().min(1),
  "grid": z.enum(["2x2","2x4","4x2","4x4","2x1","1x2"]),
  "widget_type": z.enum(["calendar","clock","alarm","task","custom"]),
  "intent": z.string().min(1),
  "attachments": z.array(z.object({
  "attachment_id": z.string().min(1),
  "role": z.enum(["visual_reference","content_extraction","data_source","style_reference"]),
  "insight_ref": z.string(),
})),
  "layout_slots": z.array(z.object({
  "slot": z.string().min(1),
  "content": z.string(),
})),
  "capabilities": z.array(z.enum(["date","timer","local_storage","static_data","theme","locale"])),
  "data_contract": z.object({
  "sources": z.array(z.enum(["time","local_storage","static"])),
  "storage_namespace": z.string().min(1),
}),
  "style": z.object({
  "palette": z.array(z.string()).optional(),
  "radius": z.number().optional(),
  "font_scale": z.number().optional(),
}),
  "constraints": z.object({
  "forbidden": z.array(z.string()),
  "max_elements": z.number().int().min(1),
}),
  "language": z.literal("tsx"),
  "react_version": z.string().min(1),
  "import_allowlist": z.literal(["react","@widget/bridge"]),
  "export_contract": z.object({
  "default": z.literal("Widget"),
}),
  "props_contract": z.literal(["grid","theme","locale","tick","data"]),
}),
  WidgetBundle: z.object({
  "bundle_id": z.string().min(1),
  "user_id": z.string().min(1),
  "session_id": z.string().min(1),
  "spec": Schemas.widget_spec.schema.json,
  "source": z.object({
  "path": z.literal("widget.tsx"),
  "content_ref": z.string().min(1),
  "language": z.literal("tsx"),
}),
  "compiled": z.object({
  "content_ref": z.string().min(1),
  "hash": z.string(),
}),
  "manifest": z.object({
  "spec_version": z.string().min(1),
  "spec_hash": z.string(),
  "grid": z.enum(["2x2","2x4","4x2","4x4","2x1","1x2"]),
  "widget_type": z.enum(["calendar","clock","alarm","task","custom"]),
  "capabilities": z.array(z.enum(["date","timer","local_storage","static_data","theme","locale"])),
  "import_allowlist": z.literal(["react","@widget/bridge"]),
  "exports": z.object({
  "default": z.literal("Widget"),
}),
  "props": z.array(z.string()),
  "recipe_version": z.string().min(1),
  "retrieval_snapshot": z.string().min(1),
  "model": z.string().min(1),
  "prompt_version": z.string().min(1),
  "model_tier": z.enum(["cheap","standard","frontier"]),
}),
  "static_check": z.object({
  "passed": z.boolean(),
  "violations": z.array(z.string()),
}),
  "sandbox_test": z.object({
  "passed": z.boolean(),
  "cases": z.array(z.object({
  "name": z.string().min(1),
  "passed": z.boolean(),
  "detail_ref": z.string().min(1),
})),
  "log_ref": z.string().min(1),
  "snapshot_ref": z.string().optional(),
}),
  "state": z.enum(["GENERATED","INSTALLED","DELETED"]),
  "hash": z.string(),
  "created_at": z.string().datetime({ offset: true }),
}),
  WidgetRuntime: z.object({
  "card": z.object({
  "grid": z.enum(["2x2","2x4","4x2","4x4","2x1","1x2"]),
  "px": z.object({
  "w": z.number().int().min(1),
  "h": z.number().int().min(1),
}),
}),
  "iframe": z.object({
  "sandbox": z.literal(["allow-scripts"]),
  "csp": z.string().min(1),
  "srcdoc_ref": z.string().min(1),
}),
  "bridge_messages": z.literal(["widget:ready","widget:resize","widget:error","host:theme","host:locale","host:tick","host:data"]),
  "capability_bridge": z.array(z.enum(["date","timer","local_storage","static_data","theme","locale"])),
  "limits": z.object({
  "render_timeout_ms": z.number().int().min(1),
  "max_message_rate": z.number().int().min(1),
}),
}),
} as const;

export const AgentEventSchema = Schemas.AgentEvent;
export const WidgetSpecSchema = Schemas.WidgetSpec;
export const WidgetBundleSchema = Schemas.WidgetBundle;
export const WidgetRuntimeSchema = Schemas.WidgetRuntime;
