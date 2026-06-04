#include <SDL2/SDL.h>
#include <SDL2/SDL_mixer.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr int kAudioFrequency = 44100;
constexpr int kAudioChannels = 2;
constexpr int kAudioChunkSize = 512;
constexpr double kDefaultVolume = 1.0;

struct ChunkDeleter {
    void operator()(Mix_Chunk* chunk) const {
        if (chunk != nullptr) {
            Mix_FreeChunk(chunk);
        }
    }
};

using ChunkPtr = std::unique_ptr<Mix_Chunk, ChunkDeleter>;

struct Options {
    bool server = false;
    bool list_devices = false;
    std::string play_file;
    std::string device;
    double volume = kDefaultVolume;
};

std::string trim(std::string value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return "";
    }

    const auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::string absolute_path(const std::string& path) {
    return fs::absolute(fs::path(path)).lexically_normal().string();
}

int volume_to_mixer(double volume) {
    volume = std::clamp(volume, 0.0, 1.0);
    return static_cast<int>(volume * MIX_MAX_VOLUME);
}

void throw_sdl_error(const std::string& prefix) {
    throw std::runtime_error(prefix + ": " + SDL_GetError());
}

void throw_mixer_error(const std::string& prefix) {
    throw std::runtime_error(prefix + ": " + Mix_GetError());
}

class AudioPlayer {
public:
    AudioPlayer(std::string device, double volume)
        : device_(std::move(device)), volume_(std::clamp(volume, 0.0, 1.0)) {}

    ~AudioPlayer() {
        Mix_HaltChannel(-1);
        chunks_.clear();
        if (mixer_open_) {
            Mix_CloseAudio();
        }
        if (mixer_initialized_) {
            Mix_Quit();
        }
        if (sdl_initialized_) {
            SDL_QuitSubSystem(SDL_INIT_AUDIO);
        }
    }

    void start() {
        if (mixer_open_) {
            return;
        }

        if (SDL_InitSubSystem(SDL_INIT_AUDIO) != 0) {
            throw_sdl_error("SDL audio init failed");
        }
        sdl_initialized_ = true;

        const int init_flags = MIX_INIT_MP3 | MIX_INIT_OGG;
        const int initialized_flags = Mix_Init(init_flags);
        if ((initialized_flags & MIX_INIT_MP3) == 0) {
            throw_mixer_error("SDL_mixer MP3 support is unavailable");
        }
        mixer_initialized_ = true;

        const char* device_name = device_.empty() ? nullptr : device_.c_str();
        if (Mix_OpenAudioDevice(
                kAudioFrequency,
                MIX_DEFAULT_FORMAT,
                kAudioChannels,
                kAudioChunkSize,
                device_name,
                SDL_AUDIO_ALLOW_FREQUENCY_CHANGE) != 0) {
            throw_mixer_error("SDL_mixer could not open audio device");
        }
        mixer_open_ = true;
        Mix_AllocateChannels(8);
    }

    void preload(const std::string& path) {
        start();
        load(path);
    }

    void play(const std::string& path, bool require_preloaded) {
        start();
        const std::string key = absolute_path(path);
        Mix_Chunk* chunk = nullptr;

        const auto existing = chunks_.find(key);
        if (existing == chunks_.end()) {
            if (require_preloaded) {
                throw std::runtime_error("audio file was not preloaded: " + key);
            }
            chunk = load(key);
        } else {
            chunk = existing->second.get();
        }

        Mix_VolumeChunk(chunk, volume_to_mixer(volume_));
        Mix_HaltChannel(-1);
        const int channel = Mix_PlayChannel(-1, chunk, 0);
        if (channel < 0) {
            throw_mixer_error("SDL_mixer could not start playback");
        }
    }

    void stop() {
        start();
        Mix_HaltChannel(-1);
    }

    void set_volume(double volume) {
        volume_ = std::clamp(volume, 0.0, 1.0);
    }

    void wait_until_idle() const {
        while (Mix_Playing(-1) > 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
    }

private:
    Mix_Chunk* load(const std::string& path) {
        const std::string key = absolute_path(path);
        const auto existing = chunks_.find(key);
        if (existing != chunks_.end()) {
            return existing->second.get();
        }

        if (!fs::is_regular_file(key)) {
            throw std::runtime_error("audio file does not exist: " + key);
        }

        ChunkPtr chunk(Mix_LoadWAV(key.c_str()));
        if (chunk == nullptr) {
            throw_mixer_error("SDL_mixer could not load " + key);
        }

        Mix_Chunk* raw = chunk.get();
        chunks_.emplace(key, std::move(chunk));
        return raw;
    }

    std::string device_;
    double volume_;
    bool sdl_initialized_ = false;
    bool mixer_initialized_ = false;
    bool mixer_open_ = false;
    std::unordered_map<std::string, ChunkPtr> chunks_;
};

Options parse_args(int argc, char** argv) {
    Options options;

    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "--server") {
            options.server = true;
        } else if (arg == "--list-devices") {
            options.list_devices = true;
        } else if (arg == "--play") {
            if (++index >= argc) {
                throw std::runtime_error("--play requires a file path");
            }
            options.play_file = argv[index];
        } else if (arg == "--device") {
            if (++index >= argc) {
                throw std::runtime_error("--device requires an SDL audio device name");
            }
            options.device = argv[index];
        } else if (arg == "--volume") {
            if (++index >= argc) {
                throw std::runtime_error("--volume requires a number from 0.0 to 1.0");
            }
            options.volume = std::stod(argv[index]);
        } else if (arg == "--help" || arg == "-h") {
            std::cout
                << "Usage:\n"
                << "  audio_player --server [--device NAME] [--volume 0.8]\n"
                << "  audio_player --play FILE [--device NAME] [--volume 0.8]\n"
                << "  audio_player --list-devices\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }

    options.volume = std::clamp(options.volume, 0.0, 1.0);
    return options;
}

void list_devices() {
    if (SDL_InitSubSystem(SDL_INIT_AUDIO) != 0) {
        throw_sdl_error("SDL audio init failed");
    }

    const int count = SDL_GetNumAudioDevices(0);
    for (int index = 0; index < count; ++index) {
        const char* name = SDL_GetAudioDeviceName(index, 0);
        if (name != nullptr) {
            std::cout << name << '\n';
        }
    }
    SDL_QuitSubSystem(SDL_INIT_AUDIO);
}

std::pair<std::string, std::string> split_command(const std::string& line) {
    const std::string cleaned = trim(line);
    const auto space = cleaned.find_first_of(" \t");
    if (space == std::string::npos) {
        return {cleaned, ""};
    }

    return {cleaned.substr(0, space), trim(cleaned.substr(space + 1))};
}

void run_server(AudioPlayer& player) {
    player.start();
    std::cout << "READY" << std::endl;

    std::string line;
    while (std::getline(std::cin, line)) {
        const auto [command, argument] = split_command(line);

        try {
            if (command == "PRELOAD") {
                player.preload(argument);
                std::cout << "OK PRELOAD" << std::endl;
            } else if (command == "PLAY") {
                player.play(argument, false);
                std::cout << "OK PLAY" << std::endl;
            } else if (command == "PLAY_PRELOADED") {
                player.play(argument, true);
                std::cout << "OK PLAY_PRELOADED" << std::endl;
            } else if (command == "PLAY_PRELOADED_BLOCKING") {
                player.play(argument, true);
                player.wait_until_idle();
                std::cout << "OK PLAY_PRELOADED_BLOCKING" << std::endl;
            } else if (command == "STOP") {
                player.stop();
                std::cout << "OK STOP" << std::endl;
            } else if (command == "VOLUME") {
                player.set_volume(std::stod(argument));
                std::cout << "OK VOLUME" << std::endl;
            } else if (command == "QUIT") {
                std::cout << "OK QUIT" << std::endl;
                return;
            } else if (!command.empty()) {
                std::cout << "ERR unknown command: " << command << std::endl;
            }
        } catch (const std::exception& error) {
            std::cout << "ERR " << error.what() << std::endl;
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const Options options = parse_args(argc, argv);

        if (options.list_devices) {
            list_devices();
            return 0;
        }

        AudioPlayer player(options.device, options.volume);

        if (options.server) {
            run_server(player);
            return 0;
        }

        if (!options.play_file.empty()) {
            player.play(options.play_file, false);
            player.wait_until_idle();
            return 0;
        }

        std::cerr << "No action requested. Use --help for usage.\n";
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "audio_player: " << error.what() << '\n';
        return 1;
    }
}
