# Production async Rust

Design async code around ownership over time. Syntax is the easy part; the
production contract is cancellation, bounded resource use, error propagation,
fairness, and shutdown.

## Contents

1. Choose async deliberately
2. Keep a synchronous functional core
3. Compose before spawning
4. Audit cancellation at every await
5. Bound concurrency and queues
6. Choose shared-state mechanisms
7. Isolate blocking and CPU work
8. Use select, timeouts, and retries safely
9. Design shutdown and cleanup
10. Design async library APIs
11. Streams, pinning, and manual futures
12. Observability and review checklist
13. Primary sources and caveats

## 1. Choose async deliberately

Use async for many concurrent operations that spend substantial time waiting,
usually on I/O, timers, or coordination. Use threads, scoped threads, Rayon, or
a bounded blocking executor for CPU-bound parallelism when those fit better.

Rust futures are inert values: calling an async function returns a future, and
work advances only while it is polled. Dropping the future cancels further
progress. There is no language-provided runtime, so runtime behavior is an
explicit dependency rather than a universal Rust guarantee.

Do not make a function async when it never awaits and is not implementing an
intentional async interface. Keep synchronous work callable from both sync and
async contexts.

## 2. Keep a synchronous functional core

Separate:

- parsing, validation, state transitions, policy, and calculations into sync
  functions over explicit values; and
- sockets, files, clocks, timers, task management, channels, and retries into
  an async orchestration layer.

Pass immutable snapshots or owned messages into the core and interpret returned
decisions at the edge. This reduces cancellation surface and makes concurrency
limits visible.

Do not hide runtime access in global helpers or constructors. Inject the values
or explicit capability the operation requires.

## 3. Compose before spawning

Prefer awaiting or composing futures in the current task:

- await sequentially when later work depends on earlier output;
- use join/try-join for a fixed set of independent operations;
- use a stream or unordered future collection with a nonzero limit for dynamic
  bounded concurrency; and
- spawn only for independent task ownership, parallel execution, isolation, or
  a lifetime that is intentionally reified outside the current call.

Treat every spawned task as a child with an owner:

- retain its join handle or register it in an owning task set/tracker;
- propagate returned errors and join failures to the parent policy;
- define what happens if the parent returns, panics, or is cancelled;
- define whether shutdown drains, cooperatively cancels, aborts, or abandons
  work; and
- wait for completion before the owner itself completes, unless the task is an
  explicitly process-lifetime service owned at the composition root.

Dropping a Tokio `JoinHandle` detaches the task; it does not cancel it. Never do
that accidentally.

## 4. Audit cancellation at every await

An async function may stop at any `.await`, including awaits expanded by a
macro. Review each await with these questions:

1. What owned local state is dropped here?
2. Has any input been consumed destructively?
3. Is shared or durable state partially updated?
4. Does dropping a guard release all synchronous resources?
5. Can the operation be restarted without duplication or loss?
6. If it cannot, where is progress stored and who resumes or compensates?

Prefer one of these shapes:

- gather and validate asynchronously, then commit a synchronous state change;
- keep partial progress in an owned state object that survives/reifies the
  operation;
- reserve a resource, perform the operation, then explicitly commit the permit;
- use an idempotency key and durable operation state at external boundaries; or
- document that cancellation abandons the operation when that is the desired
  semantics.

Do not hold a transient buffer containing destructively read data only inside a
future that may be repeatedly raced and dropped. Audit the documented cancel
safety of every future used inside `select`.

Cancellation tokens are cooperative. A future can still be dropped or aborted
without observing its token, so token handling does not replace local
cancellation safety.

Analyze caller-driven cancellation separately from an error produced inside
the operation. When composed futures are owned by the returned future, dropping
that outer future drops the local future graph and its accumulator, so those
futures cease being polled. This abandons their Rust-side progress; it does not
promise that a request already handed to an external system is halted or rolled
back. When a fallible stream stops on an error, also define the fate of pending
futures and any results accumulated before the error. A normal fail-fast
collection discards that partial collection unless the API deliberately returns
partial success.

## 5. Bound concurrency and queues

Every source of concurrency or queuing needs a limit and overload policy:

- channel capacity;
- in-flight requests or stream items;
- accepted connections;
- spawned tasks;
- retries and retry duration;
- blocking jobs and CPU parallelism; and
- buffered response or request bodies.

Prefer bounded channels. When full, choose deliberately among waiting,
rejecting, shedding, coalescing, or replacing stale work. An unbounded channel
is valid only when a separate hard bound proves the producer cannot outrun the
consumer over the component lifetime.

Use semaphores or limited stream combinators to cap work, but preserve permits
through the actual resource lifetime. Avoid a zero value that an API interprets
as unlimited.

Make queue capacity a consequence of memory budget, service time, burst size,
and latency objective—not an arbitrary large number.

## 6. Choose shared-state mechanisms

Use ordinary ownership first.

- For short, synchronous, low-contention access that never spans `.await`, a
  standard mutex can be appropriate even in async code.
- For a resource whose operations are themselves async, prefer one owner task
  plus typed messages when this matches the domain.
- Use an async mutex only when exclusive access genuinely must span an await.
  Keep the critical section small and never perform unrelated I/O while locked.
- Use read/write locks only when measured read concurrency justifies their
  fairness and overhead trade-offs.
- Use atomics only with an explicit memory-ordering and state-transition model.

Move data or owned handles into tasks. Use `Arc` for intentional cross-task
shared ownership, not as a default wrapper around every service.

## 7. Isolate blocking and CPU work

Never perform blocking I/O, long synchronous waits, or substantial CPU work on
an executor worker thread.

- Use the runtime's blocking bridge for bounded, eventually completing
  operations.
- Bound CPU-heavy blocking jobs separately; Tokio's blocking-thread upper limit
  is large by default.
- Consider a dedicated thread or Rayon for sustained CPU workloads.
- Move owned inputs into blocking work and return owned results.
- Account for shutdown: a started Tokio blocking task cannot be aborted.
- Do not wrap an infinite or long-lived blocking loop in `spawn_blocking` when a
  dedicated managed thread is the clearer owner.

When bridging sync and async code, use documented channel or I/O bridge methods
and preserve backpressure in both directions.

## 8. Use select, timeouts, and retries safely

`select` returns the first completed branch and cancels the others. Before using
it:

- verify cancellation safety of every losing branch;
- ensure no branch can starve another;
- understand whether biased selection changes fairness;
- preserve any in-progress state outside a repeatedly recreated future; and
- distinguish timeout from cancellation, remote failure, and overload in the
  returned error.

Apply timeouts at meaningful external or queue boundaries, not blindly around
every call. Decide whether elapsed work is safe to abandon and whether the
underlying operation continues elsewhere.

Retries must be bounded by attempt count and/or deadline. Retry only errors
classified as transient, use backoff and jitter when appropriate, and require
idempotency or compensation for side effects. A retry loop is an effectful state
machine; prefer clear loop syntax over an opaque combinator chain.

## 9. Design shutdown and cleanup

Model graceful shutdown as a protocol:

1. Detect a shutdown condition.
2. Signal owned tasks cooperatively.
3. Stop accepting new work.
4. Drain, finish, or cancel queued and in-flight work according to policy.
5. Flush or close resources through explicit async methods.
6. Join all owned tasks and surface failures.

Use RAII for synchronous cleanup, but do not rely on `Drop` for async work.
Provide an explicit `shutdown`, `close`, or consuming completion method when
cleanup must await or report errors. Keep fallback `Drop` behavior infallible and
nonblocking.

## 10. Design async library APIs

- Keep the core runtime-neutral when practical. Return futures or streams and
  let the application choose where to spawn.
- If an API is runtime-specific, make that dependency explicit in the module,
  type, feature, or crate boundary.
- Avoid starting hidden background tasks from a constructor. If unavoidable,
  return an owner that exposes shutdown and task failure.
- Document cancellation semantics, blocking behavior, ordering, concurrency,
  queue bounds, and runtime requirements in the public API's rustdoc. For a
  collection-producing operation, also say whether an early error discards
  completed items and what caller cancellation does to locally owned in-flight
  futures.
- Decide whether public trait futures must be `Send`. Multithreaded spawning
  commonly requires it; local executors may not.
- Decide whether dynamic dispatch is required. Native async trait methods and
  returned `impl Future` do not automatically solve object-safe dispatch.
- Reconcile async trait syntax, return bounds, capture rules, and any macro
  choice with the crate's MSRV and supported executors.

Do not force `'static` or `Send` onto every API solely because one adapter uses
`tokio::spawn`; keep those bounds at the ownership boundary that needs them.

## 11. Streams, pinning, and manual futures

Use streams for asynchronous sequences. Choose ordered buffering when result
order matters and unordered buffering when completion order is acceptable.
Always provide a concurrency limit for dynamic work.

Prefer `async fn`, async blocks, and established combinators. Implement
`Future`, `Stream`, or pin-projection machinery manually only when building a
low-level reusable primitive or when measurement proves the need.

When manual polling is necessary:

- never block in `poll`;
- register/wake correctly whenever progress may become possible;
- uphold pinning and structural projection invariants;
- avoid waking unconditionally and causing busy loops; and
- document cancellation and drop behavior of partially completed state.

## 12. Observability and review checklist

Instrument task and request boundaries with stable identifiers and structured
context. Avoid holding tracing spans or guards in ways that make futures
non-`Send` or extend critical sections unintentionally.

Review:

- Is async justified by waiting/concurrency rather than syntax preference?
- Is deterministic work synchronous and separately composable?
- Does every spawned task have an owner, join path, and error policy?
- Is each await cancellation-safe or explicitly documented otherwise?
- Are queues, concurrency, retries, and blocking work bounded?
- Are locks held for the minimum duration and never accidentally across await?
- Are select branches cancel-safe and fair?
- Is blocking work isolated and shutdown-aware?
- Does graceful shutdown stop intake, clean up, and join?
- Are runtime, `Send`, dynamic dispatch, and MSRV contracts explicit?

## 13. Primary sources and caveats

- [The Rust Book: async fundamentals](https://doc.rust-lang.org/stable/book/ch17-00-async-await.html)
- [Async Rust Book](https://rust-lang.github.io/async-book/)
- [Async Rust Book: cancellation discussion](https://rust-lang.github.io/async-book/part-guide/more-async-await.html)
- [Async Rust Book: structured concurrency](https://rust-lang.github.io/async-book/part-reference/structured.html)
- [Rust Reference: async functions](https://doc.rust-lang.org/stable/reference/items/functions.html#async-functions)
- [Standard Future trait](https://doc.rust-lang.org/stable/std/future/trait.Future.html)
- [Tokio select cancellation safety](https://docs.rs/tokio/latest/tokio/macro.select.html#cancellation-safety)
- [Tokio bounded channels and backpressure](https://tokio.rs/tokio/tutorial/channels)
- [Tokio shared state](https://tokio.rs/tokio/tutorial/shared-state)
- [Tokio blocking bridge](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html)
- [Tokio graceful shutdown](https://tokio.rs/tokio/topics/shutdown)
- [Rust Async Working Group: async traits](https://blog.rust-lang.org/2023/12/21/async-fn-rpit-in-traits/)

Verified 2026-08-16. The Async Rust Book is being rewritten and labels parts of
its advanced material as rough or incomplete. Use it for language-level mental
models, then verify exact cancellation, fairness, locking, task, and shutdown
behavior in the selected runtime and crate versions.
