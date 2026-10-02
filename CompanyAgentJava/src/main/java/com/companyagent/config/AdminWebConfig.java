package com.companyagent.config;

import com.companyagent.api.AdminAuthInterceptor;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

@Configuration
public class AdminWebConfig implements WebMvcConfigurer {

    /** Paths that require an administrator token; also used to mark them in the OpenAPI document. */
    public static final List<String> ADMIN_PATHS = List.of(
            "/admin/overview",
            "/skills",
            "/skills/reload",
            "/monitor",
            "/search",
            "/knowledge/**",
            "/eval/run"
    );

    private final AdminAuthInterceptor adminAuthInterceptor;

    public AdminWebConfig(AdminAuthInterceptor adminAuthInterceptor) {
        this.adminAuthInterceptor = adminAuthInterceptor;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(adminAuthInterceptor).addPathPatterns(ADMIN_PATHS);
    }
}
