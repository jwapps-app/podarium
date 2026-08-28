import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount anything a test rendered. Without this a component stays mounted into the next
// test, still holding its timers -- which is precisely what several of these tests assert
// about.
afterEach(cleanup);
