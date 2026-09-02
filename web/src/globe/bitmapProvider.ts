/**
 * Custom ImageryProvider returning pre-decoded ImageBitmaps — Architecture.md §6.1.
 *
 * One provider serves exactly one frame of one layer as a single level-0 tile in
 * a rectangle-limited GeographicTilingScheme (the same shape as Cesium's own
 * SingleTileImageryProvider, which is the documented fallback if this misbehaves).
 *
 * A provider over ImageBitmaps solves four problems at once: decode happens off
 * the frame path (createImageBitmap on fetch'd WebP blobs), filters can be set at
 * layer construction, tile pyramids are avoided entirely, and images stay exactly
 * the pixels the encoder wrote.
 */
import {
  Event as CesiumEvent,
  GeographicTilingScheme,
  Rectangle,
  type Credit,
  type ImageryProvider,
  type Request,
  type TileDiscardPolicy,
} from "cesium";

/**
 * Ring buffer of decoded ImageBitmaps keyed by URL, LRU-evicted.
 * Decode is colour-exact: no premultiplication, no colour-space conversion —
 * the authored byte must be the uploaded byte (§3.7: the pipeline is a
 * pass-through once the four widget lines are set).
 */
export class BitmapRing {
  private cache = new Map<string, Promise<ImageBitmap>>();

  /**
   * onDecoded matters under requestRenderMode: Cesium only re-requests renders
   * when ITS RequestScheduler completes a request, and this ring bypasses the
   * scheduler by design. Without a nudge per decoded bitmap, the imagery state
   * machine starves and the globe never becomes renderable.
   */
  constructor(
    private capacity = 48,
    private onDecoded?: () => void,
  ) {}

  get(url: string): Promise<ImageBitmap> {
    const hit = this.cache.get(url);
    if (hit) {
      // refresh LRU position
      this.cache.delete(url);
      this.cache.set(url, hit);
      return hit;
    }
    const promise = fetch(url)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`frame fetch failed: ${response.status} ${response.statusText} for ${url}`);
        }
        return response.blob();
      })
      .then((blob) =>
        createImageBitmap(blob, {
          premultiplyAlpha: "none",
          colorSpaceConversion: "none",
        }),
      )
      .then((bitmap) => {
        this.onDecoded?.();
        return bitmap;
      })
      .catch((err) => {
        // A rejected promise must not poison the cache forever, but the error
        // itself propagates to every awaiting consumer — never swallowed.
        this.cache.delete(url);
        throw err;
      });
    this.cache.set(url, promise);
    this.evict();
    return promise;
  }

  prefetch(urls: string[]): void {
    for (const url of urls) {
      // Surface prefetch failures loudly; the same rejection also reaches any
      // later awaiting consumer via get().
      this.get(url).catch((err) => console.error("[latent-sky] prefetch:", err));
    }
  }

  private evict(): void {
    while (this.cache.size > this.capacity) {
      const oldest = this.cache.keys().next().value as string;
      const evicted = this.cache.get(oldest);
      this.cache.delete(oldest);
      evicted?.then((bmp) => bmp.close()).catch(() => undefined /* already surfaced in get() */);
    }
  }

  /**
   * Close and drop every cached bitmap. Called on event switches (after the
   * outgoing event's ImageryLayers have been removed, so no live layer can be
   * awaiting one of these) and at teardown. Pending fetches are closed when
   * they resolve, so an in-flight decode cannot leak either.
   */
  clear(): void {
    for (const p of this.cache.values()) {
      p.then((bmp) => bmp.close()).catch(() => undefined /* already surfaced in get() */);
    }
    this.cache.clear();
  }

  destroy(): void {
    this.clear();
  }
}

export interface BitmapProviderOptions {
  /** Geographic extent of the image, degrees [west, south, east, north]. */
  rect: [number, number, number, number];
  /** Image pixel size [width, height]. */
  size: [number, number];
  /** Supplies the (pre-decoded, cached) ImageBitmap for this frame. */
  source: () => Promise<ImageBitmap>;
}

/**
 * The property surface mirrors SingleTileImageryProvider in 1.143.0 exactly
 * (verified against its d.ts): tilingScheme, rectangle, tileWidth, tileHeight,
 * maximumLevel, minimumLevel, tileDiscardPolicy, errorEvent, credit, proxy,
 * hasAlphaChannel, getTileCredits, requestImage, pickFeatures.
 */
export class BitmapImageryProvider {
  readonly rectangle: Rectangle;
  readonly tilingScheme: GeographicTilingScheme;
  readonly tileWidth: number;
  readonly tileHeight: number;
  readonly maximumLevel = 0;
  readonly minimumLevel = 0;
  readonly tileDiscardPolicy: TileDiscardPolicy | undefined = undefined;
  readonly errorEvent = new CesiumEvent();
  readonly credit: Credit | undefined = undefined;
  readonly proxy = undefined;
  readonly hasAlphaChannel = true;

  private readonly source: () => Promise<ImageBitmap>;

  constructor(options: BitmapProviderOptions) {
    const [west, south, east, north] = options.rect;
    this.rectangle = Rectangle.fromDegrees(west, south, east, north);
    this.tilingScheme = new GeographicTilingScheme({
      rectangle: this.rectangle,
      numberOfLevelZeroTilesX: 1,
      numberOfLevelZeroTilesY: 1,
    });
    [this.tileWidth, this.tileHeight] = options.size;
    this.source = options.source;
  }

  getTileCredits(_x: number, _y: number, _level: number): Credit[] | undefined {
    return undefined;
  }

  requestImage(_x: number, _y: number, _level: number, _request?: Request): Promise<ImageBitmap> {
    return this.source();
  }

  pickFeatures(_x: number, _y: number, _level: number, _longitude: number, _latitude: number): undefined {
    return undefined;
  }

  /** The structural cast Cesium's typings require for a custom provider. */
  asProvider(): ImageryProvider {
    return this as unknown as ImageryProvider;
  }
}
