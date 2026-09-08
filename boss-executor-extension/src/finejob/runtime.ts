import { BossChatCoordinator } from "./chat-coordinator";
import { FineJobExecutorClient } from "./client";
import { SharedActionGate } from "./shared-action-gate";

// Background 在此处创建唯一 gate，并把同一实例交给两个既有执行循环。
export const sharedActionGate = new SharedActionGate();
export const fineJobExecutorClient = new FineJobExecutorClient(sharedActionGate);
export const bossChatCoordinator = new BossChatCoordinator(fineJobExecutorClient, sharedActionGate);
