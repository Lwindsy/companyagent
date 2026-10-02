package com.companyagent.config;

import io.swagger.v3.oas.models.Components;
import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.info.License;
import io.swagger.v3.oas.models.security.SecurityRequirement;
import io.swagger.v3.oas.models.security.SecurityScheme;
import org.springdoc.core.customizers.OpenApiCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.AntPathMatcher;

@Configuration
public class OpenApiConfig {

    private static final String ADMIN_SCHEME = "AdminBearer";

    // No servers are declared: springdoc derives the server URL from each request, and with
    // server.forward-headers-strategy=framework that includes the proxy's X-Forwarded-Prefix,
    // so "Try it out" works both locally and behind /api/java.
    @Bean
    public OpenAPI companyAgentOpenAPI() {
        return new OpenAPI()
                .info(new Info()
                        .title("CompanyAgent Java API")
                        .version("0.1.0")
                        .description("CompanyAgent Java 智能客服系统接口文档，支持在线调试 /chat、/search、知识库、监控和评测接口。")
                        .license(new License().name("Internal Project")))
                .components(new Components().addSecuritySchemes(ADMIN_SCHEME, new SecurityScheme()
                        .type(SecurityScheme.Type.HTTP)
                        .scheme("bearer")
                        .description("Token from POST /admin/login")));
    }

    /** Marks the endpoints guarded by AdminAuthInterceptor so Swagger UI sends the token for them. */
    @Bean
    public OpenApiCustomizer adminSecurityCustomizer() {
        AntPathMatcher matcher = new AntPathMatcher();
        return openApi -> {
            if (openApi.getPaths() == null) {
                return;
            }
            openApi.getPaths().forEach((path, item) -> {
                boolean admin = AdminWebConfig.ADMIN_PATHS.stream().anyMatch(pattern -> matcher.match(pattern, path));
                if (admin) {
                    item.readOperations().forEach(op -> op.addSecurityItem(new SecurityRequirement().addList(ADMIN_SCHEME)));
                }
            });
        };
    }
}
