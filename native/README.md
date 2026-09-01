# ForgeCommunicator (Combined Native App)

ForgeCommunicator is a unified macOS desktop shell for managing multiple chat sources from one native app, including Communicator, WhatsApp Web, Signal sessions, and Telegram Web.

This MVP does not reverse engineer protocols, ingest messages, bypass platform security, or sync any cloud data.

## Stack Choice

- Native framework: SwiftUI (macOS)
- Embedded web container: WKWebView (for WhatsApp Web)
- Local security: CryptoKit AES-GCM + Keychain-managed key
- Local persistence: encrypted JSON config in Application Support

## Current Features

- Sidebar source list
- Main workspace pane
- Add source (Communicator, WhatsApp, Signal)
- Rename source
- Remove source
- Reorder sources in sidebar
- Per-source local profile directory creation
- Encrypted local source metadata config
- Native shell with Communicator-inspired visual design (gradient atmosphere + glass surfaces)
- Communicator web workspace source with source-level server URL config
- WhatsApp web workspace container per source
- Signal isolated process launch attempt per source profile (when Signal.app exists)
- Telegram Web source support

## Source Metadata Shape

Each source is persisted with:

- id (UUID)
- type (communicator, whatsapp, signal, telegram)
- displayName
- profilePath
- createdAt
- lastOpenedAt
- providerConfig (encrypted JSON)

## Run

1. Build and run the macOS app bundle from Xcode using the `ForgeInboxLite` scheme.
2. Do not use `swift run` for normal app usage.

`swift run` launches the Swift package executable directly from the build directory rather than from a `.app` bundle. That breaks app-bundle-dependent behavior used by this project, including parts of WebKit and notifications.

## Security and Privacy Notes

- No central backend is used.
- No cloud sync is used.
- Account metadata is encrypted at rest.
- The encryption key is stored in the macOS Keychain.

## Current Technical Caveats

- WhatsApp source isolation is currently implemented with separate in-memory web sessions and per-source cookie persistence files.
- Full browser-storage isolation parity with dedicated Chromium user profiles is not implemented in this MVP.
- Communicator source currently uses an embedded workspace web session while native API/WebSocket integration is being migrated.
- Signal integration launches Signal Desktop with a source-specific profile argument where supported by the local Signal binary behavior.

## Project Structure

- Package.swift
- Sources/ForgeInboxLite/App/ForgeInboxLiteApp.swift
- Sources/ForgeInboxLite/Views/Shell/ForgeCommunicatorShellView.swift
- Sources/ForgeInboxLite/Views/AddAccountSheet.swift
- Sources/ForgeInboxLite/Models/Account.swift
- Sources/ForgeInboxLite/Models/SourceConfig.swift
- Sources/ForgeInboxLite/Providers/SourceProvider.swift
- Sources/ForgeInboxLite/Providers/CommunicatorProvider.swift
- Sources/ForgeInboxLite/Providers/WhatsAppProvider.swift
- Sources/ForgeInboxLite/Providers/SignalProvider.swift
- Sources/ForgeInboxLite/Services/AccountStore.swift
- Sources/ForgeInboxLite/Services/EncryptedConfigStore.swift
- Sources/ForgeInboxLite/Services/NotificationService.swift
- Sources/ForgeInboxLite/Services/SignalLauncher.swift
- Sources/ForgeInboxLite/UI/ForgeTheme.swift
- Sources/ForgeInboxLite/UI/ForgeBackgroundLayer.swift
- Sources/ForgeInboxLite/UI/ForgeGlassSurface.swift
- Sources/ForgeInboxLite/UI/ForgeBadgeIcon.swift
- Sources/ForgeInboxLite/UI/ForgeHeaderBar.swift
- Sources/ForgeInboxLite/Web/AccountWebContainerView.swift
