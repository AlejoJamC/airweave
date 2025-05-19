export const protectedPaths = {
    dashboard: "/",
    collections: "/collections",
    collectionDetail: "/collections/:readable_id",
    apiKeys: "/api-keys",
    whiteLabel: "/white-label",
    whiteLabelTab: "/white-label/:id",
    whiteLabelCreate: "/white-label/create",
    whiteLabelDetail: "/white-label/:id",
    authCallback: "/auth/callback/:short_name",
}

export const publicPaths = {
    login: "/login",
    callback: "/callback",
}
