#!/bin/bash

# EchoMind 镜像构建脚本
# 提供多种构建选项供不同场景使用

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 配置
IMAGE_NAME="echomind"
REGISTRY=""  # 如果需要推送到私有仓库，设置为 registry.example.com/
VERSION=${VERSION:-latest}
BUILD_ARGS=""

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# 显示使用帮助
show_help() {
    cat << EOF
EchoMind Docker 镜像构建工具

用法: ./build-image.sh [命令] [选项]

命令:
    build           构建默认镜像
    build-prod      构建生产镜像
    build-dev       构建开发镜像
    build-test      构建测试镜像
    push            推送镜像到仓库
    tag             为镜像添加标签
    clean           清理构建缓存
    help            显示此帮助

选项:
    --no-cache      禁用缓存构建
    --platform      指定平台 (linux/amd64, linux/arm64)
    --registry      指定镜像仓库
    --version       指定版本号

示例:
    ./build-image.sh build-prod
    ./build-image.sh build --no-cache
    ./build-image.sh build --platform linux/amd64,linux/arm64
    ./build-image.sh push --registry my-registry.com
    ./build-image.sh tag --version v1.0.0

EOF
}

# 构建镜像
build_image() {
    local target=$1
    local no_cache=$2
    local platforms=$3

    print_step "开始构建镜像: ${IMAGE_NAME}:${VERSION}"

    # 构建参数
    build_cmd="docker build"

    # 添加目标
    if [ -n "$target" ]; then
        build_cmd="$build_cmd --target $target"
    fi

    # 添加缓存选项
    if [ "$no_cache" = "true" ]; then
        build_cmd="$build_cmd --no-cache"
        print_warn "禁用构建缓存"
    fi

    # 添加多平台支持
    if [ -n "$platforms" ]; then
        build_cmd="$build_cmd --platform $platforms"
        print_info "构建平台: $platforms"
    fi

    # 添加构建参数
    if [ -n "$BUILD_ARGS" ]; then
        build_cmd="$build_cmd $BUILD_ARGS"
    fi

    # 执行构建
    full_tag="${REGISTRY}${IMAGE_NAME}:${VERSION}"
    build_cmd="$build_cmd -t $full_tag -t ${REGISTRY}${IMAGE_NAME}:latest ."

    print_info "执行命令: $build_cmd"
    eval $build_cmd

    if [ $? -eq 0 ]; then
        print_info "✓ 镜像构建成功: $full_tag"
    else
        print_error "✗ 镜像构建失败"
        exit 1
    fi
}

# 推送镜像
push_image() {
    local registry=$1

    if [ -n "$registry" ]; then
        REGISTRY="$registry/"
    fi

    print_step "推送镜像到仓库"

    # 推送版本标签
    docker push ${REGISTRY}${IMAGE_NAME}:${VERSION}

    # 推送最新标签
    docker push ${REGISTRY}${IMAGE_NAME}:latest

    print_info "✓ 镜像推送成功"
}

# 为镜像添加标签
tag_image() {
    local new_version=$1

    print_step "为镜像添加标签: $new_version"

    docker tag ${REGISTRY}${IMAGE_NAME}:${VERSION} ${REGISTRY}${IMAGE_NAME}:${new_version}

    print_info "✓ 标签添加成功: ${REGISTRY}${IMAGE_NAME}:${new_version}"
}

# 清理构建缓存
clean_build_cache() {
    print_step "清理 Docker 构建缓存"

    docker builder prune -f

    print_info "✓ 构建缓存已清理"
}

# 解析参数
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --no-cache)
                NO_CACHE=true
                shift
                ;;
            --platform)
                PLATFORMS="$2"
                shift 2
                ;;
            --registry)
                REGISTRY="$2"
                shift 2
                ;;
            --version)
                VERSION="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done
}

# 主函数
main() {
    local command=${1:-help}
    shift || true

    # 解析选项
    parse_args "$@"

    case $command in
        build)
            build_image "" "$NO_CACHE" "$PLATFORMS"
            ;;
        build-prod)
            build_image "production" "$NO_CACHE" "$PLATFORMS"
            ;;
        build-dev)
            build_image "development" "$NO_CACHE" "$PLATFORMS"
            ;;
        build-test)
            build_image "test" "$NO_CACHE" "$PLATFORMS"
            ;;
        push)
            shift
            push_image "$1"
            ;;
        tag)
            shift
            tag_image "$1"
            ;;
        clean)
            clean_build_cache
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "未知命令: $command"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"