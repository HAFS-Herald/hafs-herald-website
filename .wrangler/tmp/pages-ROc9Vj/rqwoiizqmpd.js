// <define:__ROUTES__>
var define_ROUTES_default = {
  version: 1,
  include: ["/", "/index.html", "/article", "/article.html"],
  exclude: []
};

// ../../../tmp/claude-0/-home-user-hafs-herald-website/2ea7f969-1bf7-50d1-998c-fccaa3c49946/scratchpad/node_modules/wrangler/templates/pages-dev-pipeline.ts
import worker from "/home/user/hafs-herald-website/.wrangler/tmp/pages-ROc9Vj/functionsWorker-0.3702878443171953.mjs";
import { isRoutingRuleMatch } from "/tmp/claude-0/-home-user-hafs-herald-website/2ea7f969-1bf7-50d1-998c-fccaa3c49946/scratchpad/node_modules/wrangler/templates/pages-dev-util.ts";
export * from "/home/user/hafs-herald-website/.wrangler/tmp/pages-ROc9Vj/functionsWorker-0.3702878443171953.mjs";
var routes = define_ROUTES_default;
var pages_dev_pipeline_default = {
  fetch(request, env, context) {
    const { pathname } = new URL(request.url);
    for (const exclude of routes.exclude) {
      if (isRoutingRuleMatch(pathname, exclude)) {
        return env.ASSETS.fetch(request);
      }
    }
    for (const include of routes.include) {
      if (isRoutingRuleMatch(pathname, include)) {
        const workerAsHandler = worker;
        if (workerAsHandler.fetch === void 0) {
          throw new TypeError("Entry point missing `fetch` handler");
        }
        return workerAsHandler.fetch(request, env, context);
      }
    }
    return env.ASSETS.fetch(request);
  }
};
export {
  pages_dev_pipeline_default as default
};
//# sourceMappingURL=rqwoiizqmpd.js.map
