export type SharedActionGateState = "idle" | "busy" | "cooldown";

/** 后台唯一的真实外发门，协调现有 greeting 与 chat 两条执行循环。 */
export class SharedActionGate {
  private state: SharedActionGateState = "idle";

  tryEnterBusy(): boolean {
    if (this.state !== "idle") return false;
    this.state = "busy";
    return true;
  }

  releaseBusy(): void {
    if (this.state === "busy") this.state = "idle";
  }

  enterCooldown(): boolean {
    if (this.state === "cooldown") return false;
    this.state = "cooldown";
    return true;
  }

  finishCooldown(): void {
    if (this.state === "cooldown") this.state = "idle";
  }

  isActive(): boolean {
    return this.state !== "idle";
  }

  isCooldownActive(): boolean {
    return this.state === "cooldown";
  }
}
