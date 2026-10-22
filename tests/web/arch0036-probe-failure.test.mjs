import test from "node:test";

test("disposable probe fails Node", () => {
  throw new Error("intentional disposable CI smoke probe");
});
