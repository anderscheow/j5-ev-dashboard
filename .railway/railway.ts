import { defineRailway, github, project, service } from "railway/iac";

// Separate Railway project and service. Production remains on its existing host.
export default defineRailway(() => {
  const dashboard = service("tiggo-7-phev-staging", {
    source: github("anderscheow/j5-ev-dashboard", { branch: "staging" }),
    healthcheck: "/api/status",
    healthcheckTimeout: 60,
    env: {
      CARLINKO_DEMO: "1",
      PORT: "8088",
      PYTHONUNBUFFERED: "1",
    },
  });

  return project("tiggo-7-phev-staging", {
    resources: [dashboard],
  });
});
