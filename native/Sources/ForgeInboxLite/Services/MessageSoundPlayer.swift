import AVFoundation
import Foundation

class MessageSoundPlayer {
    static let shared = MessageSoundPlayer()

    private init() {}

    private var player: AVAudioPlayer?

    var soundEnabled: Bool {
        get {
            let defaults = UserDefaults.standard
            if defaults.object(forKey: "forge.soundEnabled") == nil {
                return true
            }
            return defaults.bool(forKey: "forge.soundEnabled")
        }
        set {
            UserDefaults.standard.set(newValue, forKey: "forge.soundEnabled")
        }
    }

    func play() {
        guard soundEnabled else { return }

        guard let url = Bundle.main.url(forResource: "chirp", withExtension: "mp3") else {
            print("MessageSoundPlayer: chirp.mp3 not found in bundle")
            return
        }

        do {
            let audioPlayer = try AVAudioPlayer(contentsOf: url)
            audioPlayer.volume = 0.7
            audioPlayer.play()
            player = audioPlayer
        } catch {
            print("MessageSoundPlayer: failed to create AVAudioPlayer - \(error)")
        }
    }
}
