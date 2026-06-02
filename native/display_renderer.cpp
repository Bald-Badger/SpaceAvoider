#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <cairo/cairo.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr const char* kDefaultFramebuffer = "/dev/fb0";
constexpr const char* kDefaultFramebufferSysfs = "/sys/class/graphics/fb0";
constexpr int kDefaultRadius = 80;
constexpr int kDefaultSeconds = 4;

struct Options {
    bool corner_circle_demo = false;
    fs::path framebuffer = kDefaultFramebuffer;
    fs::path framebuffer_sysfs = kDefaultFramebufferSysfs;
    int radius = kDefaultRadius;
    int seconds = kDefaultSeconds;
};

struct FramebufferInfo {
    int width = 0;
    int height = 0;
    int bits_per_pixel = 0;
    int stride = 0;

    int bytes_per_pixel() const {
        return bits_per_pixel / 8;
    }

    std::size_t buffer_bytes() const {
        return static_cast<std::size_t>(height) * static_cast<std::size_t>(stride);
    }
};

class Framebuffer {
public:
    Framebuffer(fs::path device, FramebufferInfo info)
        : device_(std::move(device)), info_(info) {
        fd_ = open(device_.c_str(), O_RDWR | O_CLOEXEC);
        if (fd_ < 0) {
            throw std::runtime_error("could not open framebuffer " + device_.string());
        }

        memory_ = mmap(nullptr, info_.buffer_bytes(), PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
        if (memory_ == MAP_FAILED) {
            close(fd_);
            fd_ = -1;
            throw std::runtime_error("could not mmap framebuffer " + device_.string());
        }
    }

    ~Framebuffer() {
        if (memory_ != nullptr && memory_ != MAP_FAILED) {
            munmap(memory_, info_.buffer_bytes());
        }
        if (fd_ >= 0) {
            close(fd_);
        }
    }

    Framebuffer(const Framebuffer&) = delete;
    Framebuffer& operator=(const Framebuffer&) = delete;

    const FramebufferInfo& info() const {
        return info_;
    }

    void clear(std::uint32_t color) {
        for (int y = 0; y < info_.height; ++y) {
            auto* row = row_ptr(y);
            for (int x = 0; x < info_.width; ++x) {
                row[x] = color;
            }
        }
    }

    void write_full_frame(const std::uint8_t* pixels, int source_stride) {
        const auto visible_row_bytes = static_cast<std::size_t>(info_.width) * info_.bytes_per_pixel();
        if (source_stride == info_.stride) {
            std::memcpy(memory_, pixels, info_.buffer_bytes());
            return;
        }

        auto* target = static_cast<std::uint8_t*>(memory_);
        for (int y = 0; y < info_.height; ++y) {
            std::memcpy(
                target + static_cast<std::size_t>(y) * info_.stride,
                pixels + static_cast<std::size_t>(y) * source_stride,
                visible_row_bytes);
        }
    }

private:
    std::uint32_t* row_ptr(int y) {
        auto* base = static_cast<std::uint8_t*>(memory_);
        return reinterpret_cast<std::uint32_t*>(base + static_cast<std::size_t>(y) * info_.stride);
    }

    fs::path device_;
    FramebufferInfo info_;
    int fd_ = -1;
    void* memory_ = nullptr;
};

class CairoFrame {
public:
    CairoFrame(int width, int height) : width_(width), height_(height) {
        stride_ = cairo_format_stride_for_width(CAIRO_FORMAT_ARGB32, width_);
        if (stride_ <= 0) {
            throw std::runtime_error("Cairo rejected the framebuffer width");
        }

        pixels_.resize(static_cast<std::size_t>(stride_) * static_cast<std::size_t>(height_));
        surface_ = cairo_image_surface_create_for_data(
            pixels_.data(),
            CAIRO_FORMAT_ARGB32,
            width_,
            height_,
            stride_);
        if (cairo_surface_status(surface_) != CAIRO_STATUS_SUCCESS) {
            throw std::runtime_error("could not create Cairo image surface");
        }
    }

    ~CairoFrame() {
        if (surface_ != nullptr) {
            cairo_surface_destroy(surface_);
        }
    }

    CairoFrame(const CairoFrame&) = delete;
    CairoFrame& operator=(const CairoFrame&) = delete;

    void draw_corner_circle(int center_x, int center_y, int radius, double red, double green, double blue) {
        cairo_surface_flush(surface_);
        auto* cr = cairo_create(surface_);
        if (cairo_status(cr) != CAIRO_STATUS_SUCCESS) {
            cairo_destroy(cr);
            throw std::runtime_error("could not create Cairo context");
        }

        cairo_set_source_rgb(cr, 0.0, 0.0, 0.0);
        cairo_paint(cr);

        cairo_set_source_rgb(cr, red, green, blue);
        cairo_arc(cr, center_x, center_y, radius, 0.0, 2.0 * 3.14159265358979323846);
        cairo_fill(cr);

        const cairo_status_t status = cairo_status(cr);
        cairo_destroy(cr);
        cairo_surface_flush(surface_);
        if (status != CAIRO_STATUS_SUCCESS) {
            throw std::runtime_error("Cairo draw failed");
        }
    }

    const std::uint8_t* pixels() const {
        return pixels_.data();
    }

    int stride() const {
        return stride_;
    }

private:
    int width_;
    int height_;
    int stride_;
    std::vector<std::uint8_t> pixels_;
    cairo_surface_t* surface_ = nullptr;
};

std::string read_text(const fs::path& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("could not read " + path.string());
    }

    std::string value;
    std::getline(input, value);
    return value;
}

FramebufferInfo read_framebuffer_info(const fs::path& sysfs_dir) {
    const std::string virtual_size = read_text(sysfs_dir / "virtual_size");
    const auto comma = virtual_size.find(',');
    if (comma == std::string::npos) {
        throw std::runtime_error("invalid framebuffer virtual_size: " + virtual_size);
    }

    FramebufferInfo info;
    info.width = std::stoi(virtual_size.substr(0, comma));
    info.height = std::stoi(virtual_size.substr(comma + 1));
    info.bits_per_pixel = std::stoi(read_text(sysfs_dir / "bits_per_pixel"));
    info.stride = std::stoi(read_text(sysfs_dir / "stride"));

    if (info.width <= 0 || info.height <= 0 || info.stride <= 0) {
        throw std::runtime_error("invalid framebuffer geometry");
    }
    if (info.bits_per_pixel != 32) {
        throw std::runtime_error("display_renderer currently supports only 32 bpp framebuffers");
    }
    if (info.stride < info.width * info.bytes_per_pixel()) {
        throw std::runtime_error("framebuffer stride is smaller than visible row bytes");
    }

    return info;
}

void run_corner_circle_demo(const Options& options) {
    const FramebufferInfo info = read_framebuffer_info(options.framebuffer_sysfs);
    Framebuffer framebuffer(options.framebuffer, info);
    CairoFrame frame(info.width, info.height);
    const int radius = std::clamp(options.radius, 4, std::max(4, std::min(info.width, info.height) / 2));
    const int seconds = std::max(1, options.seconds);

    const std::vector<std::pair<int, int>> corners = {
        {radius, radius},
        {info.width - radius - 1, radius},
        {info.width - radius - 1, info.height - radius - 1},
        {radius, info.height - radius - 1},
    };
    const std::vector<std::tuple<double, double, double>> colors = {
        {0.0, 0.86, 0.47},
        {0.31, 0.67, 1.0},
        {1.0, 0.35, 0.35},
        {1.0, 0.90, 0.27},
    };

    std::cout << "display_renderer framebuffer "
              << info.width << "x" << info.height
              << " bpp=" << info.bits_per_pixel
              << " stride=" << info.stride
              << " cairo_stride=" << frame.stride()
              << " radius=" << radius << '\n';

    const auto started_at = std::chrono::steady_clock::now();
    double total_render_ms = 0.0;
    double total_write_ms = 0.0;
    for (int step = 0; step < seconds; ++step) {
        const std::size_t corner_index = static_cast<std::size_t>(step) % corners.size();
        const auto [red, green, blue] = colors[corner_index % colors.size()];
        const auto render_started_at = std::chrono::steady_clock::now();
        frame.draw_corner_circle(
            corners[corner_index].first,
            corners[corner_index].second,
            radius,
            red,
            green,
            blue);
        const auto render_finished_at = std::chrono::steady_clock::now();

        framebuffer.write_full_frame(frame.pixels(), frame.stride());
        const auto write_finished_at = std::chrono::steady_clock::now();

        total_render_ms += std::chrono::duration<double, std::milli>(
            render_finished_at - render_started_at).count();
        total_write_ms += std::chrono::duration<double, std::milli>(
            write_finished_at - render_finished_at).count();

        std::this_thread::sleep_until(started_at + std::chrono::seconds(step + 1));
    }

    std::cout << "display_renderer avg_render_ms=" << total_render_ms / seconds
              << " avg_full_fb_write_ms=" << total_write_ms / seconds << '\n';
}

Options parse_args(int argc, char** argv) {
    Options options;

    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "--corner-circle-demo") {
            options.corner_circle_demo = true;
        } else if (arg == "--framebuffer") {
            if (++index >= argc) {
                throw std::runtime_error("--framebuffer requires a path");
            }
            options.framebuffer = argv[index];
        } else if (arg == "--framebuffer-sysfs") {
            if (++index >= argc) {
                throw std::runtime_error("--framebuffer-sysfs requires a path");
            }
            options.framebuffer_sysfs = argv[index];
        } else if (arg == "--radius") {
            if (++index >= argc) {
                throw std::runtime_error("--radius requires a number");
            }
            options.radius = std::stoi(argv[index]);
        } else if (arg == "--seconds") {
            if (++index >= argc) {
                throw std::runtime_error("--seconds requires a number");
            }
            options.seconds = std::stoi(argv[index]);
        } else if (arg == "--help" || arg == "-h") {
            std::cout
                << "Usage:\n"
                << "  display_renderer --corner-circle-demo [--seconds 4] [--radius 80]\n"
                << "                   [--framebuffer /dev/fb0]\n"
                << "                   [--framebuffer-sysfs /sys/class/graphics/fb0]\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }

    return options;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const Options options = parse_args(argc, argv);
        if (options.corner_circle_demo) {
            run_corner_circle_demo(options);
            return 0;
        }

        std::cerr << "No action requested. Use --help for usage.\n";
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "display_renderer: " << error.what() << '\n';
        return 1;
    }
}
