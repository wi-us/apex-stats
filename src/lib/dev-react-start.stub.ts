export function useServerFn<T extends (...args: any[]) => any>(fn: T): T {
  return fn;
}

export function createServerFn() {
  return {
    middleware() {
      return this;
    },
    inputValidator() {
      return this;
    },
    handler<T extends (...args: any[]) => any>(handlerFn: T): T {
      return handlerFn;
    },
  };
}
