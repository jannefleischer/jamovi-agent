# jamovi-assistant: the results only host the chat widget. The widget itself
# (chat.js/chat.css) and the conversation are served by the jamovi_agent
# server extension; inst/loader.js loads them from <jamovi root>/agent/.

assistantClass <- if (requireNamespace("jmvcore", quietly = TRUE)) R6::R6Class(
    "assistantClass",
    inherit = assistantBase,
    private = list(
        .init = function() {
            panel <- self$results$panel
            panel$scripts <- "loader.js"
        },
        .run = function() {
            chat <- self$options$chat
            n <- if (nchar(chat) > 0) length(gregexpr('"role"', chat, fixed = TRUE)[[1]]) else 0
            html <- paste0(
                '<div class="jamovi-assistant-module" data-messages="', n, '">',
                '<noscript>The chat needs JavaScript.</noscript></div>')
            self$results$panel$setContent(html)
        })
)
